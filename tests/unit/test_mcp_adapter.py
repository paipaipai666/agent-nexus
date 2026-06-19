"""Backward-compatible shim — tests split into tests/unit/test_mcp/.

Run the new split tests with:
    pytest tests/unit/test_mcp/ -x -q

Or run this file directly (it re-imports all test classes):
    pytest tests/unit/test_mcp_adapter.py -x -q
"""

# Re-export all test classes from the split modules so this file remains runnable.
from tests.unit.test_mcp.test_adapter_basic import (  # noqa: F401
    TestContentBlockToText,
    TestContentBlockToTextEdgeCases,
    TestNormalizeToolResult,
    TestSanitizeName,
)
from tests.unit.test_mcp.test_adapter_config import (  # noqa: F401
    TestCreateMcpManagerFromSettings,
    TestCreateMcpManagerFromSettingsExtended,
    TestFullMcpCapabilities,
    TestMCPDescriptorSignature,
    TestRetryFailedFullFlow,
    TestStatusSnapshotValidation,
)
from tests.unit.test_mcp.test_adapter_connection import (  # noqa: F401
    TestConnectAllSuite,
    TestConnectServerFailurePaths,
    TestHttpConnectServer,
    TestHttpInitializeFailure,
    TestStdioConnectServer,
)
from tests.unit.test_mcp.test_adapter_lifecycle import (  # noqa: F401
    TestCallLockConcurrency,
    TestEventLoopLifecycle,
    TestResourceCleanup,
    TestRunLoopExceptionHandling,
    TestStartIdempotency,
    TestStartSuccess,
    TestSubmitRealLoop,
    TestTimeoutBehavior,
)
from tests.unit.test_mcp.test_adapter_manager import (  # noqa: F401
    TestBuildHttpClientKwargs,
    TestMcpToolManager,
)
