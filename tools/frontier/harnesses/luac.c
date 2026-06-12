/* luac frontier harness — prefix||window, luaL_loadbuffer with the exact
 * candidate length. This is the length-exact subject where stock KLEE's
 * fixed-size -sym-stdin makes accepting states unreachable; here the
 * symbolic window length supplies the missing "input ends here" forks. */
#include "frontier_common.h"

#include "lua.h"
#include "lauxlib.h"

#define HARNESS_BUF 4096

int main(int argc, char **argv) {
    static char buf[HARNESS_BUF];
    size_t total = frontier_fill(buf, HARNESS_BUF, argc, argv);

    lua_State *L = luaL_newstate();
    if (!L) return 2;

    int rc = luaL_loadbuffer(L, buf, total, "<frontier>");
    lua_close(L);
    return (rc == LUA_OK) ? 0 : 1;
}
