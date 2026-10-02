// keychain_read.cpp
// This first version goal create library that we can use find X where can anything store in keychain or password
// pair with python binding later use that in hidden gems
//
// Mustafa Bayramov mbayramov@cisco.com / spyroot@gmail.com
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>

#include "keychain_reader.hpp"

int main(const int argc, char **argv) {
    if (argc < 2) {
        std::cerr
                << "Usage: " << argv[0]
                << " <name> [name...]\n";
        return 1;
    }

    int exit_status = 0;
    for (int i = 1; i < argc; ++i) {
        const std::string name = argv[i];

        try {
            if (auto value = hiddengems::KeychainReader::read(name)) {
                std::cout
                        << name << " [keychain] = "
                        << *value << '\n';

                continue;
            }
            std::cerr << name << ": not found\n";
            if (exit_status == 0)
                exit_status = 1;
        } catch (const std::exception &error) {
            std::cerr
                    << name << ": "
                    << error.what() << '\n';
            exit_status = 2;
        }
    }

    return exit_status;
}
