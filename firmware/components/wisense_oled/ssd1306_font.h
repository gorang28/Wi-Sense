#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Glyph width in pixels (5 columns of data + 1 blank column of spacing). */
#define SSD1306_FONT_WIDTH  6
/** Glyph height in pixels. */
#define SSD1306_FONT_HEIGHT 8

/**
 * @brief Return a pointer to 5 column bytes for ASCII character c.
 *
 * Each byte is a vertical column (bit0 = top). Unsupported chars map to space.
 */
const uint8_t *ssd1306_font_glyph(char c);

#ifdef __cplusplus
}
#endif
