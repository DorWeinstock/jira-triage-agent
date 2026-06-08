#!/usr/bin/env python3
"""Quick test script for K8s MCP tools"""

import asyncio
import sys
from src.tools.k8s_tools import K8sTools


async def test_k8s_tools():
    """Test K8s MCP connection and tools"""
    tools = K8sTools('http://localhost:8084/mcp')

    try:
        print('✓ Connecting to K8s MCP server...')

        # List available tools
        print('✓ Listing available tools...')
        result = await tools.list_tools()
        print(f'✓ Found {len(result)} K8s tools')

        # Show first 5 tools
        print('\nSample tools:')
        for tool in result[:5]:
            print(f"  - {tool['name']}: {tool['description']}")

        # Test getting pods
        print('\n✓ Testing kubectl_get for pods...')
        pods = await tools.kubectl_get('pods', namespace='default')
        print(f'✓ Successfully retrieved pods from default namespace')

        # Test getting events
        print('✓ Testing kubectl_events...')
        events = await tools.kubectl_events(namespace='default')
        print(f'✓ Successfully retrieved events')

        print('\n✅ All K8s MCP tools tests passed!')

    except Exception as e:
        print(f'\n❌ Test failed: {e}')
        import traceback
        traceback.print_exc()
        return 1
    finally:
        await tools.close()

    return 0


if __name__ == '__main__':
    exit_code = asyncio.run(test_k8s_tools())
    sys.exit(exit_code)
