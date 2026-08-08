/*
 * Gradient-boosted decision tree evaluator.
 *
 * Runs the ensembles exported from scikit-learn's HistGradientBoostingClassifier
 * by python/export/export_cascade_c.py.  Everything lives in .rodata, so
 * inference allocates nothing and is safe to call from any task.
 */
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief One tree node, 12 bytes.
 *
 * @c value is the split threshold on an internal node and the leaf output on a
 * leaf — a node is never both, so sharing the field halves the table size.
 * @c left / @c right are absolute indices into the model's node array.
 */
typedef struct {
    float value;
    uint16_t left;
    uint16_t right;
    uint8_t feature;
    uint8_t is_leaf;
} wisense_gbdt_node_t;

/** One binary stage: every tree concatenated, plus each tree's root index. */
typedef struct {
    const wisense_gbdt_node_t *nodes;
    const uint16_t *tree_root;
    uint16_t n_trees;
    float baseline;
} wisense_gbdt_model_t;

/**
 * @brief Sum of every tree's leaf output plus the ensemble baseline.
 *
 * This is the log-odds ("raw") score, before the logistic link.
 *
 * @param model Exported stage.
 * @param features Feature vector; must be at least as long as the largest
 *        feature index the model splits on.
 */
float wisense_gbdt_raw_score(const wisense_gbdt_model_t *model, const float *features);

/**
 * @brief Positive-class probability, i.e. the logistic of the raw score.
 *
 * Matches sklearn's predict_proba()[:, 1] for a binary
 * HistGradientBoostingClassifier.
 */
float wisense_gbdt_predict_proba(const wisense_gbdt_model_t *model, const float *features);

#ifdef __cplusplus
}
#endif
