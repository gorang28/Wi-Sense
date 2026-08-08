#include <math.h>
#include <string.h>

#include "wisense_csi_features.h"

#define NUM_COMPLEX WISENSE_CSI_NUM_COMPLEX
#define NUM_VALID   WISENSE_CSI_NUM_VALID_SC
#define NUM_SC      WISENSE_CSI_NUM_SC

/**
 * Partition around a pivot until the k-th smallest element sits at index k.
 *
 * Only needed for the raw-int16 rescale path, which live captures never take
 * (see the short-circuit in packet_median_amplitude), so an in-place select is
 * cheaper than sorting all NUM_VALID entries on every packet.
 */
static float select_kth(float *values, int count, int k)
{
    int low = 0;
    int high = count - 1;

    while (low < high) {
        const float pivot = values[(low + high) / 2];
        int i = low;
        int j = high;
        while (i <= j) {
            while (values[i] < pivot) {
                i++;
            }
            while (values[j] > pivot) {
                j--;
            }
            if (i <= j) {
                const float swap = values[i];
                values[i] = values[j];
                values[j] = swap;
                i++;
                j--;
            }
        }
        if (k <= j) {
            high = j;
        } else if (k >= i) {
            low = i;
        } else {
            break;
        }
    }
    return values[k];
}

float wisense_csi_median_inplace(float *values, int count)
{
    if (values == NULL || count <= 0) {
        return 0.0f;
    }
    if (count % 2) {
        return select_kth(values, count, count / 2);
    }
    const float lower = select_kth(values, count, (count / 2) - 1);
    const float upper = select_kth(values, count, count / 2);
    return (lower + upper) * 0.5f;
}

/** numpy.median semantics: even-length arrays average the two middle values. */
static float packet_median_amplitude(const float *valid_amplitude, float max_amplitude)
{
    /*
     * The rescale only fires above RAW_MEDIAN_SCALE, and the median can never
     * exceed the maximum, so a small maximum rules the rescale out without
     * touching the data.  Live small-scale CSI always lands here.
     */
    if (max_amplitude <= WISENSE_CSI_RAW_MEDIAN_SCALE) {
        return max_amplitude;
    }

    float scratch[NUM_VALID];
    memcpy(scratch, valid_amplitude, sizeof(scratch));
    return wisense_csi_median_inplace(scratch, NUM_VALID);
}

bool wisense_csi_packet_amplitude(const int16_t *csi, uint16_t len, float *out_amplitude)
{
    if (csi == NULL || out_amplitude == NULL || len != NUM_COMPLEX * 2) {
        return false;
    }

    float valid_amplitude[NUM_VALID];
    float peak_iq = 0.0f;
    float max_amplitude = 0.0f;
    float amplitude_sum = 0.0f;
    bool plateau_hit = false;

    for (int index = 0; index < NUM_VALID; index++) {
        const int tone = wisense_csi_valid_sc[index];
        const float i_value = (float)csi[tone * 2];
        const float q_value = (float)csi[tone * 2 + 1];

        const float abs_i = fabsf(i_value);
        const float abs_q = fabsf(q_value);
        if (abs_i > peak_iq) {
            peak_iq = abs_i;
        }
        if (abs_q > peak_iq) {
            peak_iq = abs_q;
        }

        const float amplitude = hypotf(i_value, q_value);
        valid_amplitude[index] = amplitude;
        amplitude_sum += amplitude;
        if (amplitude > max_amplitude) {
            max_amplitude = amplitude;
        }
        if (amplitude > WISENSE_CSI_ARTIFACT_PLATEAU_LO
            && amplitude < WISENSE_CSI_ARTIFACT_PLATEAU_HI) {
            plateau_hit = true;
        }
    }

    /* _csi_format_limits(): small-scale binary CSI vs raw int16 CSV captures. */
    const bool small_scale = peak_iq <= WISENSE_CSI_MAX_VALID_IQ_ABS;
    const float amplitude_cap = small_scale ? WISENSE_CSI_MAX_VALID_AMP
                                            : WISENSE_CSI_RAW_AMP_CAP;
    const float min_good_fraction = small_scale ? WISENSE_CSI_MIN_VALID_SC_GOOD_FRAC
                                                : WISENSE_CSI_RAW_MIN_GOOD_FRAC;

    if (max_amplitude > amplitude_cap) {
        return false;
    }

    int saturated = 0;
    int good = 0;
    for (int index = 0; index < NUM_VALID; index++) {
        const float amplitude = valid_amplitude[index];
        if (amplitude >= amplitude_cap - 1.0f) {
            saturated++;
        }
        if (amplitude >= 1.0f && amplitude <= amplitude_cap) {
            good++;
        }
    }
    if ((float)saturated / (float)NUM_VALID > 0.40f) {
        return false;
    }
    if (plateau_hit && (amplitude_sum / (float)NUM_VALID) > WISENSE_CSI_ARTIFACT_MEAN_MIN) {
        return false;
    }
    if ((float)good / (float)NUM_VALID < min_good_fraction) {
        return false;
    }

    /* normalize_raw_csi_amplitudes(): bring raw int16 captures onto the same
     * scale as the live binary stream.  A uniform scale, so applying it after
     * the downsample is identical to applying it before. */
    float scale = 1.0f;
    const float median = packet_median_amplitude(valid_amplitude, max_amplitude);
    if (median > WISENSE_CSI_RAW_MEDIAN_SCALE) {
        scale = WISENSE_CSI_RAW_TARGET_MEDIAN / fmaxf(median, 1.0f);
    }

    for (int index = 0; index < NUM_SC; index++) {
        out_amplitude[index] = valid_amplitude[wisense_csi_downsample_idx[index]] * scale;
    }
    return true;
}

void wisense_csi_window_features(const wisense_csi_window_t *window, float *out_features)
{
    if (window == NULL || window->storage == NULL || out_features == NULL
        || window->count == 0) {
        return;
    }

    const uint16_t n_packets = window->count;
    const uint32_t total_values = (uint32_t)n_packets * NUM_SC;

    /*
     * Two passes for every standard deviation, matching numpy: subtract the
     * mean and then average the squared deviations.  The single-pass
     * E[x^2]-E[x]^2 shortcut loses most of its significant digits here because
     * baseline-subtracted amplitudes sit near zero with a much larger spread.
     */
    double mean_sc[NUM_SC] = {0};
    for (uint16_t packet = 0; packet < n_packets; packet++) {
        const float *row = wisense_csi_window_row(window, packet);
        for (int sc = 0; sc < NUM_SC; sc++) {
            mean_sc[sc] += row[sc];
        }
    }
    double total_sum = 0.0;
    for (int sc = 0; sc < NUM_SC; sc++) {
        total_sum += mean_sc[sc];
        mean_sc[sc] /= (double)n_packets;
    }
    const double total_mean = total_sum / (double)total_values;

    double var_sc[NUM_SC] = {0};
    double total_var = 0.0;
    double energy = 0.0;
    for (uint16_t packet = 0; packet < n_packets; packet++) {
        const float *row = wisense_csi_window_row(window, packet);
        for (int sc = 0; sc < NUM_SC; sc++) {
            const double value = row[sc];
            const double centred = value - mean_sc[sc];
            var_sc[sc] += centred * centred;
            const double total_centred = value - total_mean;
            total_var += total_centred * total_centred;
            energy += value * value;
        }
    }

    /* np.diff along the packet axis, then mean and max of the magnitudes. */
    double delta_sum = 0.0;
    double delta_max = 0.0;
    if (n_packets > 1) {
        for (uint16_t packet = 1; packet < n_packets; packet++) {
            const float *previous = wisense_csi_window_row(window, packet - 1);
            const float *current = wisense_csi_window_row(window, packet);
            for (int sc = 0; sc < NUM_SC; sc++) {
                const double delta = fabs((double)current[sc] - (double)previous[sc]);
                delta_sum += delta;
                if (delta > delta_max) {
                    delta_max = delta;
                }
            }
        }
    }
    const double delta_count = (double)(n_packets - 1) * NUM_SC;
    const double delta_mean = (n_packets > 1) ? delta_sum / delta_count : 0.0;

    /* Three subcarrier bands; the last absorbs the remainder when NUM_SC is
     * not divisible by three (32 -> 10, 10, 12), exactly as in Python. */
    const int band_edge[4] = { 0, NUM_SC / 3, 2 * (NUM_SC / 3), NUM_SC };
    double band_mean[3];
    double band_var[3];
    for (int band = 0; band < 3; band++) {
        const int first = band_edge[band];
        const int last = band_edge[band + 1];
        const uint32_t count = (uint32_t)(last - first) * n_packets;
        double sum = 0.0;
        for (uint16_t packet = 0; packet < n_packets; packet++) {
            const float *row = wisense_csi_window_row(window, packet);
            for (int sc = first; sc < last; sc++) {
                sum += row[sc];
            }
        }
        const double mean = sum / (double)count;
        double variance = 0.0;
        for (uint16_t packet = 0; packet < n_packets; packet++) {
            const float *row = wisense_csi_window_row(window, packet);
            for (int sc = first; sc < last; sc++) {
                const double centred = row[sc] - mean;
                variance += centred * centred;
            }
        }
        band_mean[band] = mean;
        band_var[band] = variance / (double)count;
    }

    int cursor = 0;
    for (int sc = 0; sc < NUM_SC; sc++) {
        out_features[cursor++] = (float)mean_sc[sc];
    }
    for (int sc = 0; sc < NUM_SC; sc++) {
        out_features[cursor++] = (float)sqrt(var_sc[sc] / (double)n_packets);
    }
    out_features[cursor++] = (float)total_mean;
    out_features[cursor++] = (float)sqrt(total_var / (double)total_values);
    out_features[cursor++] = (float)(energy / (double)total_values);
    out_features[cursor++] = (float)delta_mean;
    out_features[cursor++] = (float)delta_max;
    for (int band = 0; band < 3; band++) {
        out_features[cursor++] = (float)band_mean[band];
    }
    for (int band = 0; band < 3; band++) {
        out_features[cursor++] = (float)sqrt(band_var[band]);
    }
}
