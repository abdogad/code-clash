#include "testlib.h"
#include <vector>
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    int n = inf.readInt();
    long long t = inf.readLong();
    std::vector<long long> a(n + 1);
    for (int k = 1; k <= n; k++) a[k] = inf.readLong();
    int i = ouf.readInt(1, n, "i");
    int j = ouf.readInt(1, n, "j");
    if (i == j) quitf(_wa, "indices must be distinct");
    if (a[i] + a[j] != t) quitf(_wa, "a[%d]+a[%d] != %lld", i, j, t);
    quitf(_ok, "valid pair");
}
