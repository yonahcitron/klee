/* wren_survival.c — native survival classifier for the Wren scripting-language
 * subject (multi-file VM; compile via the public API wrenInterpret).
 *
 * Exit code is the survival signal, read from Wren's compiler control flow:
 *   exit 0  = accept  (the input COMPILES — it is syntactically valid Wren;
 *                      wrenInterpret returned SUCCESS or RUNTIME_ERROR, both of
 *                      which mean compilation already succeeded, or the alarm
 *                      fired mid-execution, which also means it had compiled)
 *   exit 10 = alive   (a COMPILE error whose message says the input ended while
 *                      more was expected — unterminated block/string, "Expect …"
 *                      at end of file, missing newline: a viable prefix. KEY)
 *   exit 11 = dead    (a COMPILE error committed on a present byte — a wrong
 *                      token a longer suffix cannot repair)
 *
 * Unlike the pure-parser subjects (luac via luaL_loadbuffer, yyjson_read,
 * sqlite3_prepare_v2 — all parse-only, no side effects), Wren has no public
 * "compile only" entry point: wrenInterpret COMPILES *then* EXECUTES the module.
 * Executing arbitrary candidate input is an EXECUTION HAZARD — e.g.
 * `while (true) {}` or `for (i in 0..1e9) {}` compiles fine and then loops
 * forever, which would hang the classifier (and any KLEE/AFL driver using it).
 * Two-part mitigation, both required:
 *
 *   1. SIGALRM guard. We install a handler that _exit(0)s and arm alarm(2)
 *      immediately before wrenInterpret. If execution hangs, the alarm fires
 *      and we exit accept (0) — which is the CORRECT classification, because a
 *      program that reached execution DID compile. (We cancel the alarm with
 *      alarm(0) the instant wrenInterpret returns, so a fast-executing or
 *      fast-erroring input is judged on its real result, not the timer.)
 *
 *   2. Compile-vs-runtime discrimination via the error callback. wrenInterpret
 *      returns COMPILE_ERROR for syntax errors but RUNTIME_ERROR for things
 *      that compiled and then failed at run time (undefined variable, type
 *      error). We only ever classify dead/alive off a *compile* error, so we
 *      record WREN_ERROR_COMPILE messages (and ONLY those — RUNTIME and
 *      STACK_TRACE callbacks are ignored) into a global, then read the message
 *      to split alive (ended-early) from dead (committed). A RUNTIME_ERROR
 *      result means it already compiled → accept.
 *
 * The alive/dead split is a substring test on the (lowercased) compiler
 * message, anchored on the error LOCATION. Empirically Wren phrases a
 * ran-out-of-input failure in exactly two ways, and a committed failure in a
 * third — and the three are cleanly separable:
 *
 *   alive  "Error at end of file: …"    — parser hit EOF wanting more, e.g.
 *          (location  "if (true) {"  -> "…Expect '}' at end of block.",
 *           is EOF)   "var x ="      -> "…Expected expression.",
 *                     "System.print(" -> "…Expect ')' after arguments."
 *   alive  "Error: Unterminated string."  /  "…Unterminated block comment."
 *          (a token  — "var x = \"abc", "/* … " : the token runs to EOF.
 *
 *   dead   "Error at '<token>': …"      — the failure is AT a concrete present
 *          (location  byte, a committed wrong token, e.g.
 *           is a      "@#$%"        -> "Error at '#': Expect end of file.",
 *           byte)     "var 123 = 4" -> "Error at '123': Expect variable name.",
 *                     "var x = 1 1" -> "Error at '1': Expect end of file."
 *
 * NOTE the trap this anchoring avoids: a *dead* message such as
 * "Error at '#': Expect end of file." itself CONTAINS the words "expect" and
 * "end of file" — but as the thing the parser *wanted*, not where it failed.
 * A naive "contains 'expect'/'eof'/'newline'" set therefore mis-files `@#$%`
 * as alive. Keying on the location phrase ("at end of file" / "unterminated"
 * means the input ran out; anything else is a present-byte commit) classifies
 * all probed inputs correctly. See REPORT for the full message battery.
 *
 * Build (native, afl-instrumented): afl-clang-fast -O2 -w -o harness_survival_afl
 *   wren_survival.c <src/vm/wren_*.c src/optional/wren_*.c>
 *   -Isrc/include -Isrc/vm -Isrc/optional -lm   (mirrors subjects/wren/build.sh).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <signal.h>

#include "wren.h"

#define HARNESS_BUF (1 << 16)

/* Compile-error state, populated only from WREN_ERROR_COMPILE callbacks. */
static int compile_err = 0;
static char last_msg[256];

static void write_noop(WrenVM *vm, const char *text) {
    (void)vm; (void)text;
}

/* Record compile-time diagnostics only; ignore RUNTIME and STACK_TRACE (those
 * mean the input already compiled and is now executing/failing at run time). */
static void error_fn(WrenVM *vm, WrenErrorType type, const char *module,
                     int line, const char *message) {
    (void)vm; (void)module; (void)line;
    if (type == WREN_ERROR_COMPILE) {
        compile_err = 1;
        if (message) {
            strncpy(last_msg, message, sizeof last_msg - 1);
            last_msg[sizeof last_msg - 1] = '\0';
        }
    }
}

/* Execution-hazard guard: if wrenInterpret hangs in EXECUTION (e.g. an infinite
 * loop), the alarm fires here. The input had to compile to start executing, so
 * accept (0) is the correct classification. */
static void on_alarm(int sig) {
    (void)sig;
    _exit(0);
}

/* True if the compiler message indicates the input ended while more was
 * expected (a viable prefix). Anchored on the error LOCATION, not on the
 * "wanted" token — see the header note on the "Error at '#': Expect end of
 * file." trap. Tuned against the validation-input message battery (REPORT):
 *   "at end of file"  -> the parser reached EOF wanting more  (alive)
 *   "unterminated"    -> a string/block-comment token runs to EOF (alive)
 *   anything else      -> "Error at '<byte>': …", a present-byte commit (dead) */
static int msg_means_ended_early(const char *msg) {
    if (!msg) return 0;
    static const char *needles[] = {
        "at end of file",  /* "Error at end of file: …" — ran out of input */
        "unterminated",    /* "Unterminated string." / "…block comment." */
    };
    for (size_t i = 0; i < sizeof needles / sizeof needles[0]; i++) {
        if (strcasestr(msg, needles[i])) return 1;
    }
    return 0;
}

int main(void) {
    static char buf[HARNESS_BUF + 1];
    ssize_t total = 0, n;
    while (total < HARNESS_BUF &&
           (n = read(0, buf + total, HARNESS_BUF - total)) > 0) {
        total += n;
    }
    buf[total] = '\0';

    WrenConfiguration cfg;
    wrenInitConfiguration(&cfg);
    cfg.writeFn = write_noop;
    cfg.errorFn = error_fn;

    WrenVM *vm = wrenNewVM(&cfg);

    /* Arm the execution-hazard guard, then compile+execute. */
    signal(SIGALRM, on_alarm);
    alarm(2);
    WrenInterpretResult r = wrenInterpret(vm, "main", buf);
    alarm(0);   /* returned in time — judge on the real result, not the timer */

    wrenFreeVM(vm);

    int out;
    if (r == WREN_RESULT_COMPILE_ERROR && compile_err) {
        /* Syntax error: ended-early → alive (10), committed → dead (11). */
        out = msg_means_ended_early(last_msg) ? 10 : 11;
    } else {
        /* SUCCESS or RUNTIME_ERROR: it compiled → accept. */
        out = 0;
    }
    return out;
}
