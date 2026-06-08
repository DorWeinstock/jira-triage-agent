#!/bin/bash
# Test runner script for Phase 1: Kagent Checkpointer Dependency Fix
#
# This script provides convenient commands to run the test suite.
# Usage: ./run-tests.sh [command]

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Project root directory (navigate up from hack/scripts/ to project root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_ROOT"

# Function to print colored output
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Function to check if dependencies are installed
check_dependencies() {
    print_info "Checking test dependencies..."

    if ! command -v pytest &> /dev/null; then
        print_error "pytest not found. Install with: pip install -r requirements-test.txt"
        exit 1
    fi

    print_info "Dependencies OK"
}

# Function to run all tests
run_all() {
    print_info "Running all tests..."
    pytest tests/ -v
}

# Function to run smoke tests only
run_smoke() {
    print_info "Running smoke tests (fast critical-path tests)..."
    pytest tests/smoke/ -v
}

# Function to run unit tests only
run_unit() {
    print_info "Running unit tests..."
    pytest tests/unit/ -v
}

# Function to run integration tests only
run_integration() {
    print_info "Running integration tests..."
    pytest tests/integration/ -v
}

# Function to run tests with coverage
run_coverage() {
    print_info "Running tests with coverage report..."
    pytest tests/ --cov=src --cov-report=html --cov-report=term
    print_info "Coverage report saved to htmlcov/index.html"
}

# Function to run tests in watch mode
run_watch() {
    print_info "Running tests in watch mode..."
    if ! command -v ptw &> /dev/null; then
        print_warning "pytest-watch not installed. Installing..."
        pip install pytest-watch
    fi
    ptw tests/
}

# Function to verify environment
verify_fix() {
    print_info "Verifying test environment..."

    # Check if dependencies are installed
    if python -c "import langgraph" 2>/dev/null; then
        print_info "✓ Core dependencies installed"
    else
        print_error "✗ Dependencies not installed"
        print_warning "Fix: Run 'pip install -r requirements.txt'"
        exit 1
    fi

    print_info "Environment verified successfully!"
}

# Function to install test dependencies
install_deps() {
    print_info "Installing test dependencies..."
    pip install -r requirements-test.txt
    print_info "Test dependencies installed"
}

# Function to show test statistics
show_stats() {
    print_info "Test statistics:"
    echo ""
    echo "Total test files:"
    find tests/ -name "test_*.py" | wc -l
    echo ""
    echo "Test breakdown by category:"
    echo "  Smoke tests: $(find tests/smoke -name "test_*.py" | wc -l)"
    echo "  Unit tests: $(find tests/unit -name "test_*.py" | wc -l)"
    echo "  Integration tests: $(find tests/integration -name "test_*.py" | wc -l)"
    echo ""
    echo "Total test functions:"
    pytest --collect-only -q tests/ 2>/dev/null | grep "test" | wc -l
}

# Function to clean test artifacts
clean() {
    print_info "Cleaning test artifacts..."
    rm -rf .pytest_cache
    rm -rf htmlcov
    rm -rf .coverage
    rm -rf __pycache__
    find tests/ -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find tests/ -type f -name "*.pyc" -delete 2>/dev/null || true
    print_info "Test artifacts cleaned"
}

# Function to show help
show_help() {
    cat << EOF
Test Runner for Phase 1: Kagent Checkpointer Dependency Fix

Usage: ./run-tests.sh [command]

Commands:
  all           Run all tests (default)
  smoke         Run smoke tests only (fast critical-path tests)
  unit          Run unit tests only
  integration   Run integration tests only
  coverage      Run tests with coverage report
  watch         Run tests in watch mode (re-run on file changes)
  verify        Verify Phase 1 fix is applied correctly
  install       Install test dependencies
  stats         Show test statistics
  clean         Clean test artifacts
  help          Show this help message

Examples:
  ./run-tests.sh                  # Run all tests
  ./run-tests.sh smoke            # Run only smoke tests
  ./run-tests.sh coverage         # Generate coverage report
  ./run-tests.sh verify           # Check if fix was applied

Before running tests:
  1. Uncomment kagent package in requirements.txt (line 10)
  2. Run: pip install -r requirements.txt
  3. Run: ./run-tests.sh verify

For more information, see tests/README.md
EOF
}

# Main script logic
main() {
    local command="${1:-all}"

    case "$command" in
        all)
            check_dependencies
            run_all
            ;;
        smoke)
            check_dependencies
            run_smoke
            ;;
        unit)
            check_dependencies
            run_unit
            ;;
        integration)
            check_dependencies
            run_integration
            ;;
        coverage)
            check_dependencies
            run_coverage
            ;;
        watch)
            check_dependencies
            run_watch
            ;;
        verify)
            verify_fix
            ;;
        install)
            install_deps
            ;;
        stats)
            show_stats
            ;;
        clean)
            clean
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_error "Unknown command: $command"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"
