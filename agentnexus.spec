# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path


def collect_data_globs(*patterns):
    datas = []
    for pattern, target in patterns:
        for path in Path().glob(pattern):
            if path.is_file():
                datas.append((str(path), target))
    return datas


datas = collect_data_globs(
    ('agentnexus/prompts/*.txt', 'agentnexus/prompts'),
    ('agentnexus/prompts/fragments/*.txt', 'agentnexus/prompts/fragments'),
    ('agentnexus/skills/builtin/*/workflow.yaml', 'agentnexus/skills/builtin'),
    ('agentnexus/builtin_extensions/*/plugin.yaml', 'agentnexus/builtin_extensions'),
    ('agentnexus/builtin_extensions/*/workflow.yaml', 'agentnexus/builtin_extensions'),
)


a = Analysis(
    ['agentnexus/__main__.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'chromadb',
        'sentence_transformers',
        'jieba',
        'pymupdf',
        'mcp',
        'mcp.client.stdio',
        'mcp.client.streamable_http',
        'openai',
        'tiktoken',
        'rich',
        'typer',
        'yaml',
        'pydantic',
        'pydantic_settings',
        'agentnexus.agents.json_helpers',
        'agentnexus.agents.llm_strategy',
        'agentnexus.agents.prompt_builder',
        'agentnexus.agents.react_runtime',
        'agentnexus.agents.tool_runner',
        'agentnexus.app.runtime',
        'agentnexus.core.pii',
        'agentnexus.core.providers',
        'agentnexus.core.providers.base',
        'agentnexus.core.providers.openai_provider',
        'agentnexus.core.providers.router',
        'agentnexus.extensions.manager',
        'agentnexus.memory.compaction',
        'agentnexus.memory.extraction',
        'agentnexus.memory.offload',
        'agentnexus.memory.projection',
        'agentnexus.observability.audit_log',
        'agentnexus.rag.citations',
        'agentnexus.rag.embeddings',
        'agentnexus.rag.kb_service',
        'agentnexus.rag.loaders',
        'agentnexus.rag.loaders.common',
        'agentnexus.rag.loaders.html',
        'agentnexus.rag.loaders.json_loader',
        'agentnexus.rag.loaders.markdown',
        'agentnexus.rag.loaders.office',
        'agentnexus.rag.loaders.pdf',
        'agentnexus.rag.loaders.text',
        'agentnexus.rag.query_expansion',
        'agentnexus.rag.ranking',
        'agentnexus.rag.store',
        'agentnexus.skills.profile',
        'agentnexus.skills.registry',
        'agentnexus.skills.runtime',
        'agentnexus.skills.workflow',
        'agentnexus.services.chat',
        'agentnexus.services.skill',
        'agentnexus.services.turn',
        'agentnexus.storage.chroma',
        'agentnexus.tools.mcp_call',
        'agentnexus.tools.mcp_capabilities',
        'agentnexus.tools.mcp_connection',
        'agentnexus.tools.mcp_descriptors',
        'agentnexus.tools.mcp_health',
        'agentnexus.tools.mcp_lifecycle',
        'agentnexus.tools.mcp_result',
        'agentnexus.tools.mcp_schema',
        'agentnexus.tools.providers',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='agentnexus',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
