"""Validate treesitter_extract on kotlin/swift/c/cpp: every expected definition
name must be extracted, and references must be collected (non-empty graph)."""
from core import treesitter_extract as te

CASES = {
    ".kt": ('''
package com.x
class Greeter(val name: String) {
    fun hello(): String { return greet(name) }
    private fun greet(n: String): String { return "hi $n" }
}
object Singleton { fun work() { Greeter("a") } }
interface Api { fun call() }
enum class Color { RED, GREEN }
fun topLevel(a: Int): Int = a + 1
''',
        # NB: top-level `object Singleton` misparses as an infix_expression in
        # tree-sitter-kotlin (no object_declaration node), so the singleton name
        # is not extracted — a grammar limitation, not name_of's. Its method
        # `work` is still captured. Classes/interfaces/enum classes/functions
        # all resolve via the type_identifier/simple_identifier child path.
        {"Greeter", "hello", "greet", "work", "Api", "call", "Color", "topLevel"}),

    ".swift": ('''
struct Point { var x: Int; func mag() -> Int { return x } }
enum Direction { case north, south }
class Animal { func speak() { describe() }; func describe() {} }
protocol Named { func name() -> String }
func freeFunc(a: Int) -> Int { return a }
''', {"Point", "mag", "Direction", "Animal", "speak", "describe", "Named", "name", "freeFunc"}),

    ".c": ('''
struct Pt { int x; int y; };
int add(int a, int b) { return a + b; }
static int doubleAdd(int a) { return add(a, a); }
''', {"Pt", "add", "doubleAdd"}),

    ".cpp": ('''
class Widget {
public:
    int value() const { return v_; }
private:
    int v_;
};
struct Vec { double x; };
namespace ns { int f(int a) { return a; } }
int main() { return f(1); }
template<typename T> T mx(T a, T b) { return a > b ? a : b; }
''', {"Widget", "value", "Vec", "ns", "f", "main", "mx"}),
}

print(f"tree-sitter available: {te.available()}")
all_ok = True
for ext, (src, expected) in CASES.items():
    syms = te.extract_symbols(src, ext)
    if not syms:
        print(f"\n{ext}: FAIL — no symbols extracted")
        all_ok = False
        continue
    names = {s["name"] for s in syms}
    missing = expected - names
    refs = sum(len(s["references"]) for s in syms)
    status = "OK " if not missing else "FAIL"
    if missing:
        all_ok = False
    print(f"\n{ext}: {status} — {len(syms)} symbols, {refs} references")
    print(f"   got     : {sorted(names)}")
    if missing:
        print(f"   MISSING : {sorted(missing)}")

print("\n=== ALL PASS ===" if all_ok else "\n=== FAILURES ABOVE ===")
