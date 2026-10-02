// Binding to Python,  note for now all error return as const string
// For now we communicate out 3 case ok / not found error
//
// Mustafa Bayramov mbayramov@cisco.com / spyroot@gmail.com

#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    HG_KEYCHAIN_OK = 0,
    HG_KEYCHAIN_NOT_FOUND = 1,
    HG_KEYCHAIN_ERROR = 2,
};

typedef int32_t HGKeychainStatus;

typedef struct HGKeychainResult {
    HGKeychainStatus status;
    unsigned char* value;
    size_t value_size;
    const char* error_message;
} HGKeychainResult;

uint32_t hg_keychain_abi_version(void);
size_t hg_keychain_result_size(void);
HGKeychainResult hg_keychain_read(const char* name);
void hg_keychain_result_free(HGKeychainResult* result);

#ifdef __cplusplus
}
#endif
