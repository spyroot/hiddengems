cmake_minimum_required(VERSION 4.3)

project(
        hiddengems
        LANGUAGES NONE
)

if(APPLE)
    enable_language(CXX)

    set(HG_READER_NAME keychainorpasswordread)
    option(HG_BUILD_READER_CLI "Build the Keychain diagnostic executable" OFF)

    find_library(
            COREFOUNDATION_FRAMEWORK
            CoreFoundation
            REQUIRED
    )

    find_library(
            SECURITY_FRAMEWORK
            Security
            REQUIRED
    )

    add_library(
            ${HG_READER_NAME}_c
            SHARED
            src/hiddengems/gems/keychain_bridge.cpp
            src/hiddengems/gems/keychain_bridge.h
            src/hiddengems/gems/keychain_reader.cpp
            src/hiddengems/gems/keychain_reader.hpp
    )

    target_include_directories(
            ${HG_READER_NAME}_c
            PUBLIC
            src/hiddengems/gems
    )

    set_target_properties(
            ${HG_READER_NAME}_c
            PROPERTIES
            CXX_STANDARD 23
            CXX_STANDARD_REQUIRED YES
            OUTPUT_NAME "${HG_READER_NAME}"
    )

    target_link_libraries(
            ${HG_READER_NAME}_c
            PRIVATE
            ${COREFOUNDATION_FRAMEWORK}
            ${SECURITY_FRAMEWORK}
    )

    install(
            TARGETS ${HG_READER_NAME}_c
            LIBRARY DESTINATION hiddengems/gems
    )

    if(HG_BUILD_READER_CLI)
        add_executable(
                ${HG_READER_NAME}
                src/hiddengems/gems/${HG_READER_NAME}.cpp
                src/hiddengems/gems/keychain_reader.cpp
        )

        target_include_directories(
                ${HG_READER_NAME}
                PRIVATE
                src/hiddengems/gems
        )

        set_target_properties(
                ${HG_READER_NAME}
                PROPERTIES
                CXX_STANDARD 23
                CXX_STANDARD_REQUIRED YES
        )

        target_link_libraries(
                ${HG_READER_NAME}
                PRIVATE
                ${COREFOUNDATION_FRAMEWORK}
                ${SECURITY_FRAMEWORK}
        )
    endif()
endif()
