/* LightRE analysis candidate
 * binary: minilua
 * sha256: 0432af2744d5f5fcc49d1acbb6c5ad0ff04d7cb62455f0ec2606e3420601e050
 * function_rva: 0x00023314
 * status: unverified (分析候选，未验证)
 */

#include <stddef.h>

/* Inferred from the 15 saved instructions, not from the reference source.
 * runtime (context + 0x20) and pending (runtime + 0x50) are provisional names.
 * Their business meaning and the helper's side effects remain unknown.
 * This models a 64-bit address space; no execution comparison has been done.
 */
typedef struct Runtime {
    unsigned char unknown_00[0x50];
    void *pending;
} Runtime;

typedef struct Context {
    unsigned char unknown_00[0x20];
    Runtime *runtime;
} Context;

/* Original helper RVA: 0x0000ab94. */
extern void fcn_0000ab94(Context *context);

/* Original function RVA: 0x00023314. */
void fcn_00023314(Context *context)
{
    while (context->runtime->pending != NULL) {
        fcn_0000ab94(context);
    }
}
