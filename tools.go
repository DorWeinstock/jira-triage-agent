//go:build tools
// +build tools

package tools

// This file declares tool dependencies that are not directly imported in code.
// It ensures these dependencies are tracked in go.mod.
// See: https://github.com/golang/go/wiki/Modules#how-can-i-track-tool-dependencies-for-a-module

import (
	_ "go.uber.org/zap"
)
