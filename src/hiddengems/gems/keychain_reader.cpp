// Binding to Python,  note for now all error return as const string
// design choice zero memory allocation.
// what need to be next added capability to enumerate if keychain contain
// same duplicate value
//
//
// Mustafa Bayramov mbayramov@cisco.com / spyroot@gmail.com
#include "keychain_reader.hpp"
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>

#include <stdexcept>
#include <string>

namespace hiddengems {
namespace {

struct SearchField {
    CFStringRef item_class;
    CFStringRef attribute;
};

std::optional<std::string> query(
    CFStringRef item_class,
    CFStringRef attribute,
    const std::string& name)
{
    CFStringRef name_ref = CFStringCreateWithCString(
        nullptr,
        name.c_str(),
        kCFStringEncodingUTF8);
    if (name_ref == nullptr)
        throw std::runtime_error("Cannot convert keychain name");

    const void* keys[] = {
        kSecClass,
        attribute,
        kSecReturnData,
        kSecMatchLimit,
    };
    const void* values[] = {
        item_class,
        name_ref,
        kCFBooleanTrue,
        kSecMatchLimitOne,
    };

    const CFDictionaryRef request = CFDictionaryCreate(
        nullptr,
        keys,
        values,
        4,
        &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks);

    CFRelease(name_ref);
    if (request == nullptr)
        throw std::runtime_error("Cannot create keychain query");

    CFTypeRef result = nullptr;
    const OSStatus status = SecItemCopyMatching(request, &result);
    CFRelease(request);

    if (status == errSecItemNotFound)
        return std::nullopt;

    if (status != errSecSuccess) {
        throw std::runtime_error(
            "Keychain query failed: " + std::to_string(status));
    }

    if (result == nullptr || CFGetTypeID(result) != CFDataGetTypeID()) {
        if (result != nullptr)
            CFRelease(result);
        throw std::runtime_error("Keychain returned unexpected data");
    }

    const auto data = static_cast<CFDataRef>(result);
    const auto size = static_cast<std::size_t>(CFDataGetLength(data));
    std::string value;
    if (size != 0) {
        value.assign(
            reinterpret_cast<const char*>(CFDataGetBytePtr(data)),
            size);
    }
    CFRelease(result);
    return value;
}

} // namespace

std::optional<std::string> KeychainReader::read(const std::string& name)
{
    static const SearchField fields[] = {
        {kSecClassGenericPassword, kSecAttrLabel},
        {kSecClassGenericPassword, kSecAttrService},
        {kSecClassGenericPassword, kSecAttrAccount},
        {kSecClassInternetPassword, kSecAttrLabel},
        {kSecClassInternetPassword, kSecAttrServer},
        {kSecClassInternetPassword, kSecAttrAccount},
    };

    for (const auto& [item_class, attribute] : fields) {
        if (auto value = query(item_class, attribute, name))
            return value;
    }
    return std::nullopt;
}

} // namespace hiddengems
