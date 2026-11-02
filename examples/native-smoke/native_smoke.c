/* Freestanding Android ARM64 test library: no libc, JNI, VM or initialization. */
__attribute__((visibility("default")))
long long smoke_arithmetic(int a, int b) {
    return (long long)a * 3 - (long long)b;
}

__attribute__((visibility("default")))
int smoke_transform(const unsigned char *input, unsigned length,
                    unsigned char *output, unsigned capacity, unsigned key) {
    if (capacity < length) return -1;
    for (unsigned i = 0; i < length; ++i)
        output[i] = input[i] ^ (unsigned char)(key + i);
    return (int)length;
}
