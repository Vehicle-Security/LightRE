/* Thin memory-input ABI; the original Linux source/build remain unchanged. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>

static size_t capture_fwrite(const void *, size_t, size_t, FILE *);
static int capture_fprintf(FILE *, const char *, ...);
#define fwrite capture_fwrite
#define fprintf capture_fprintf
#define main minilua_cli_main
#ifndef LIGHTRE_MINILUA_SOURCE
#define LIGHTRE_MINILUA_SOURCE "minilua.c"
#endif
#include LIGHTRE_MINILUA_SOURCE
#undef main
#undef fprintf
#undef fwrite

#define API __attribute__((visibility("default")))
#define CAPACITY (1024 * 1024)
static struct {
  char text[3][CAPACITY]; /* stdout, stderr, complete pointer-free Proto listing */
  uint32_t length[3];
  int overflow;
} result;

static void append(int which, const void *data, size_t size) {
  if (size > CAPACITY - 1 - result.length[which]) {
    result.overflow = 1;
    return;
  }
  memcpy(result.text[which] + result.length[which], data, size);
  result.length[which] += (uint32_t)size;
  result.text[which][result.length[which]] = 0;
}

static size_t capture_fwrite(const void *data, size_t size, size_t count, FILE *file) {
  if (file != stdout && file != stderr) return fwrite(data, size, count, file);
  if (size && count > SIZE_MAX / size) return 0;
  append(file == stdout ? 0 : 1, data, size * count);
  return result.overflow ? 0 : count;
}

static int capture_fprintf(FILE *file, const char *format, ...) {
  va_list ap, copy;
  va_start(ap, format);
  if (file != stdout && file != stderr) {
    int n = vfprintf(file, format, ap);
    va_end(ap);
    return n;
  }
  va_copy(copy, ap);
  int size = vsnprintf(NULL, 0, format, copy);
  va_end(copy);
  if (size < 0) { va_end(ap); return -1; }
  char *buffer = malloc((size_t)size + 1);
  if (!buffer) { va_end(ap); result.overflow = 1; return -1; }
  vsnprintf(buffer, (size_t)size + 1, format, ap);
  va_end(ap);
  append(file == stdout ? 0 : 1, buffer, (size_t)size);
  free(buffer);
  return result.overflow ? -1 : size;
}

static void proto_format(const char *format, ...) {
  char buffer[256];
  va_list ap;
  va_start(ap, format);
  int size = vsnprintf(buffer, sizeof(buffer), format, ap);
  va_end(ap);
  if (size < 0 || (size_t)size >= sizeof(buffer)) { result.overflow = 1; return; }
  append(2, buffer, (size_t)size);
}

static void proto_string(const TString *string) {
  size_t i;
  for (i = 0; i < string->tsv.len; ++i)
    proto_format("%02x", (unsigned char)getstr(string)[i]);
}

static void dump_proto(const Proto *p, unsigned int *next, int parent) {
  unsigned int id = (*next)++;
  int i;
  proto_format("P %u parent=%d nups=%u params=%u vararg=%u stack=%u code=%d constants=%d children=%d\n",
               id, parent, p->nups, p->numparams, p->is_vararg, p->maxstacksize,
               p->sizecode, p->sizek, p->sizep);
  proto_format("SOURCE %u ", id);
  if (p->source) proto_string(p->source);
  proto_format("\n");
  for (i = 0; i < p->sizecode; ++i)
    proto_format("I %u %d %08x line=%d\n", id, i, (unsigned int)p->code[i],
                 i < p->sizelineinfo ? p->lineinfo[i] : -1);
  for (i = 0; i < p->sizek; ++i) {
    const TValue *v = &p->k[i];
    proto_format("K %u %d type=%d ", id, i, ttype(v));
    switch (ttype(v)) {
      case 0: proto_format("nil"); break;
      case 1: proto_format("%d", bvalue(v)); break;
      case 3: proto_format("%.17g", nvalue(v)); break;
      case 4: proto_string(rawtsvalue(v)); break;
      default: result.overflow = 1; break;
    }
    proto_format("\n");
  }
  for (i = 0; i < p->sizep; ++i) dump_proto(p->p[i], next, (int)id);
}

/* Serial calls only. Each call creates and closes its own Lua state.
 * 0=success; positive values are actual Lua parser/runtime statuses;
 * -1=allocation failure; -2=output/Proto overflow; -3=invalid input.
 */
API int32_t lightre_minilua_run(const char *source, uint32_t length,
                               const char *chunk, const char *argument) {
  memset(&result, 0, sizeof(result));
  if (!source || !chunk || length > CAPACITY) return -3;
  lua_State *L = luaL_newstate();
  if (!L) return -1;
  luaL_openlibs(L);
  luaL_register(L, "bit", bitlib);
  lua_createtable(L, 0, 1);
  lua_pushstring(L, chunk);
  lua_rawseti(L, -2, 0);
  lua_setglobal(L, "arg");
  char *name = malloc(strlen(chunk) + 2);
  if (!name) { lua_close(L); return -1; }
  name[0] = '@'; strcpy(name + 1, chunk);
  int status = luaL_loadbuffer(L, source, length, name);
  free(name);
  if (!status) {
    unsigned int next = 0;
    dump_proto(clvalue(L->top - 1)->l.p, &next, -1);
    if (argument) lua_pushstring(L, argument);
    status = lua_pcall(L, argument ? 1 : 0, 0, 0);
  }
  if (status) capture_fprintf(stderr, "Error: %s\n", lua_tostring(L, -1));
  lua_close(L);
  return result.overflow ? -2 : status;
}

API const char *lightre_minilua_result(uint32_t which) {
  return which < 3 ? result.text[which] : NULL;
}

API uint32_t lightre_minilua_length(uint32_t which) {
  return which < 3 ? result.length[which] : 0;
}

/* Exposes the actual VM's address for observation, never a substitute VM. */
API void *lightre_minilua_vm_entry(void) { return (void *)luaV_execute; }
