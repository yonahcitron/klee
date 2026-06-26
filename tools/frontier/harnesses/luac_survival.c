/* luac_survival.c — native survival classifier for run13's hash-intern
 * boundary subject (luac, Lua 5.4 lexer+parser via luaL_loadbuffer).
 *
 * Exit code is the true survival signal, read from the parser's control flow:
 *   exit 0  = accept  (luaL_loadbuffer == LUA_OK: the chunk compiled)
 *   exit 10 = alive   (LUA_ERRSYNTAX whose message carries the '<eof>' token:
 *                      the lexer reached end-of-input wanting more — exactly
 *                      the "incomplete statement" test Lua's own REPL uses to
 *                      decide whether to read another line; a viable prefix)
 *   exit 11 = dead    (LUA_ERRSYNTAX on a present byte: committed wrong)
 *
 * Lua is length-exact (luaL_loadbuffer parses the whole buffer; there is no
 * getchar), so "wants more" is observed from the error token, not from reading
 * past end. This is the luac analogue of tinyc_survival.c's read-past-end.
 *
 * Build: clang -O2 -DLUA_USE_POSIX -Wno-everything luac_survival.c <lua core
 * .c files> -o harness_survival -lm  (see build-run13.sh).
 */
#include <stdio.h>
#include <string.h>
#include "lua.h"
#include "lauxlib.h"

#define BUF (1 << 16)

int main(void) {
    static char buf[BUF];
    size_t n = fread(buf, 1, sizeof buf, stdin);

    lua_State *L = luaL_newstate();
    if (!L) return 2;

    int rc = luaL_loadbuffer(L, buf, n, "<frontier>");
    int out;
    if (rc == LUA_OK) {
        out = 0;
    } else {
        const char *msg = lua_tostring(L, -1);
        out = (msg && strstr(msg, "<eof>")) ? 10 : 11;
    }
    lua_close(L);
    return out;
}
