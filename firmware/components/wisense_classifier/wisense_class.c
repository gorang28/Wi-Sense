/*
 * Shared helpers for wisense_class_t (labels, etc.).
 */
#include "wisense_classifier.h"

const char *wisense_class_to_string(wisense_class_t cls)
{
    switch (cls) {
    case WISENSE_CLASS_EMPTY:
        return "Empty";
    case WISENSE_CLASS_PRESENCE:
        return "Presence";
    case WISENSE_CLASS_MOTION:
        return "Motion";
    case WISENSE_CLASS_FALL:
        return "Fall";
    default:
        return "Unknown";
    }
}
