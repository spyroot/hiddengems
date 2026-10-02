// Binding to Python,  note for now all error return as const string
// design choice zero memory allocation.
// what need to be next added capability to enumerate if keychain contain
// same duplicate value
//
//
// Mustafa Bayramov mbayramov@cisco.com / spyroot@gmail.com

#include "keychain_bridge.h"
#include "keychain_reader.hpp"

#include <cstdlib>
#include <cstring>
#include <exception>
#include <string>

namespace {

constexpr uint32_t kAbiVersion = 1;
constexpr char kInvalidName[] = "Keychain lookup name must not be empty";
constexpr char kAllocationFailed[] = "Cannot allocate keychain result";
constexpr char kLookupFailed[] = "Keychain lookup failed";
constexpr char kUnknownError[] = "Unknown keychain error";

HGKeychainResult error_result(const char* message)
{
    return {
        HG_KEYCHAIN_ERROR,
        nullptr,
        0,
        message,
    };
}

} // namespace

extern "C" uint32_t hg_keychain_abi_version()
{
    return kAbiVersion;
}

extern "C" std::size_t hg_keychain_result_size()
{
    return sizeof(HGKeychainResult);
}

extern "C" HGKeychainResult hg_keychain_read(const char* name)
{
    if (name == nullptr || name[0] == '\0')
        return error_result(kInvalidName);

    try {
        const auto value = hiddengems::KeychainReader::read(name);
        if (!value)
            return {
            .status = HG_KEYCHAIN_NOT_FOUND,
            .value = nullptr,
            .value_size = 0,
            .error_message = nullptr
            };

        auto* bytes = static_cast<unsigned char*>(
            std::malloc(value->empty() ? 1 : value->size()));
        if (bytes == nullptr)
            return error_result(kAllocationFailed);
        if (!value->empty())
            std::memcpy(bytes, value->data(), value->size());

        return {
            .status = HG_KEYCHAIN_OK,
            .value = bytes,
            .value_size = value->size(),
            .error_message = nullptr,
        };
    } catch (const std::exception&) {
        return error_result(kLookupFailed);
    } catch (...) {
        return error_result(kUnknownError);
    }
}

extern "C" void hg_keychain_result_free(HGKeychainResult* result)
{
    if (result == nullptr)
        return;

    std::free(result->value);
    result->status = HG_KEYCHAIN_ERROR;
    result->value = nullptr;
    result->value_size = 0;
    result->error_message = nullptr;
}
