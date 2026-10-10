// SPDX-License-Identifier: AGPL-3.0-only
// Known-answer tests for the OpenSSL-compatible MD5 shim (patch 0005) that replaces <openssl/md5.h> in the
// headless build. The .gcode.3mf writer stores plate_1.gcode.md5 from it, so a wrong digest would be a silent
// firmware-side rejection.
#include <catch2/catch_all.hpp>

#include <cstdio>
#include <string>

#include "libslic3r/Utils.hpp"  // pulls in Md5Shim.hpp under SLIC3R_HEADLESS_MINIMAL

namespace {
std::string md5_hex(const std::string &s, size_t chunk = 0)
{
    MD5_CTX ctx;
    MD5_Init(&ctx);
    if (chunk == 0)
        MD5_Update(&ctx, s.data(), s.size());
    else
        for (size_t i = 0; i < s.size(); i += chunk)
            MD5_Update(&ctx, s.data() + i, std::min(chunk, s.size() - i));
    unsigned char out[MD5_DIGEST_LENGTH];
    MD5_Final(out, &ctx);
    std::string hex;
    char buf[3];
    for (unsigned char c : out) {
        std::snprintf(buf, sizeof buf, "%02x", c);
        hex += buf;
    }
    return hex;
}
} // namespace

TEST_CASE("Md5Shim known vectors (RFC 1321)", "[md5]")
{
    CHECK(md5_hex("") == "d41d8cd98f00b204e9800998ecf8427e");
    CHECK(md5_hex("abc") == "900150983cd24fb0d6963f7d28e17f72");
    CHECK(md5_hex("message digest") == "f96b697d7cb7938d525a2f31aaf161d0");
    CHECK(md5_hex("12345678901234567890123456789012345678901234567890123456789012345678901234567890") ==
          "57edf4a22be3c955ac49da2e2107b67a");
}

TEST_CASE("Md5Shim is independent of the update chunking", "[md5]")
{
    const std::string data(10007, 'x');
    CHECK(md5_hex(data, 1) == md5_hex(data));
    CHECK(md5_hex(data, 63) == md5_hex(data));
    CHECK(md5_hex(data, 4096) == md5_hex(data));
}
