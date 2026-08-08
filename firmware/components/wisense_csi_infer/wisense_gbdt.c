#include <math.h>

#include "wisense_gbdt.h"

float wisense_gbdt_raw_score(const wisense_gbdt_model_t *model, const float *features)
{
    if (model == NULL || features == NULL) {
        return 0.0f;
    }

    /*
     * Accumulate in double.  200 leaf values summed in float32 drift far enough
     * from scikit-learn's float64 sum to flip a probability sitting on a stage
     * threshold, which is exactly where the cascade makes its decisions.
     */
    double accumulator = (double)model->baseline;

    for (uint16_t tree = 0; tree < model->n_trees; tree++) {
        const wisense_gbdt_node_t *node = &model->nodes[model->tree_root[tree]];
        while (!node->is_leaf) {
            /* sklearn's split rule is "<= threshold goes left". */
            node = (features[node->feature] <= node->value)
                       ? &model->nodes[node->left]
                       : &model->nodes[node->right];
        }
        accumulator += (double)node->value;
    }

    return (float)accumulator;
}

float wisense_gbdt_predict_proba(const wisense_gbdt_model_t *model, const float *features)
{
    return 1.0f / (1.0f + expf(-wisense_gbdt_raw_score(model, features)));
}
