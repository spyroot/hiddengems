#pragma once

#include <optional>
#include <string>

namespace hiddengems {

    class KeychainReader {
    public:
        [[nodiscard]]
        static std::optional<std::string> read(
            const std::string& name);

        // Add later:
        // void put(const std::string& name,
        //          const std::string& value);
    };

} // namespace hiddengems
