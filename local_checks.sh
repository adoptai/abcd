#!/bin/bash

RUN_RUFF_CHECK=false
RUN_RUFF_FORMAT=false
RUN_MYPY=false
RUN_ALL=false

for arg in "$@"; do
    case $arg in
        --run-ruff-check)
            RUN_RUFF_CHECK=true
            ;;
        --run-ruff-format)
            RUN_RUFF_FORMAT=true
            ;;
        --run-mypy)
            RUN_MYPY=true
            ;;
        --run-all)
            RUN_ALL=true
            ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --run-ruff-check    Run ruff lint check"
            echo "  --run-ruff-format   Run ruff format check"
            echo "  --run-mypy          Run mypy type checking"
            echo "  --run-all           Run all checks"
            exit 1
            ;;
    esac
done

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 [options]"
    echo "Options:"
    echo "  --run-ruff-check    Run ruff lint check"
    echo "  --run-ruff-format   Run ruff format check"
    echo "  --run-mypy          Run mypy type checking"
    echo "  --run-all           Run all checks"
    exit 1
fi

if [ "$RUN_ALL" = true ]; then
    RUN_RUFF_CHECK=true
    RUN_RUFF_FORMAT=true
    RUN_MYPY=true
fi

if [ "$RUN_RUFF_CHECK" = true ]; then
    if ! command -v poetry &> /dev/null; then
        echo "Error: poetry is not installed."
        echo "Install it by running: pip install poetry"
        exit 1
    fi

    echo "Running ruff check..."
    poetry run ruff check --output-format=github cli/

    if [ $? -ne 0 ]; then
        echo "Error: ruff check failed."
        exit 1
    fi

    echo "✅ ruff check passed."
fi

if [ "$RUN_RUFF_FORMAT" = true ]; then
    if ! command -v poetry &> /dev/null; then
        echo "Error: poetry is not installed."
        echo "Install it by running: pip install poetry"
        exit 1
    fi

    echo "Running ruff format check..."
    poetry run ruff format --check cli/

    if [ $? -ne 0 ]; then
        echo "Error: ruff format check failed. Run 'poetry run ruff format cli/' to fix."
        exit 1
    fi

    echo "✅ ruff format check passed."
fi

if [ "$RUN_MYPY" = true ]; then
    if ! command -v poetry &> /dev/null; then
        echo "Error: poetry is not installed."
        echo "Install it by running: pip install poetry"
        exit 1
    fi

    echo "Ensuring Poetry dependencies are installed..."
    poetry install --no-interaction --no-ansi

    echo "Installing type stubs..."
    poetry run pip install types-requests --quiet

    echo "Clearing mypy cache..."
    rm -rf .mypy_cache

    echo "Running mypy..."
    poetry run mypy \
        --warn-unused-configs \
        --disallow-any-generics \
        --disallow-subclassing-any \
        --disallow-untyped-calls \
        --disallow-untyped-defs \
        --disallow-incomplete-defs \
        --check-untyped-defs \
        --warn-redundant-casts \
        --warn-unused-ignores \
        --warn-return-any \
        --no-implicit-reexport \
        --strict-equality \
        --extra-checks \
        --ignore-missing-imports \
        --show-error-codes \
        --explicit-package-bases \
        --namespace-packages \
        cli/

    if [ $? -ne 0 ]; then
        echo "Error: mypy check failed."
        exit 1
    fi

    echo "✅ mypy check passed."
fi

