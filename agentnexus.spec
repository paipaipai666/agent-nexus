# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

project_root = Path(SPECPATH)


def collect_data_globs(*patterns):
    datas = []
    for pattern, target in patterns:
        for path in project_root.glob(pattern):
            if path.is_file():
                destination = str(Path(target) / path.parent.name) if '*' in Path(pattern).parent.parts else target
                datas.append((str(path), destination))
    return datas


datas = collect_data_globs(
    ('agentnexus/prompts/*.txt', 'agentnexus/prompts'),
    ('agentnexus/prompts/fragments/*.txt', 'agentnexus/prompts/fragments'),
    ('agentnexus/skills/builtin/*/workflow.yaml', 'agentnexus/skills/builtin'),
    ('agentnexus/builtin_extensions/*/plugin.yaml', 'agentnexus/builtin_extensions'),
    ('agentnexus/builtin_extensions/*/workflow.yaml', 'agentnexus/builtin_extensions'),
) + copy_metadata('agentnexus')


a = Analysis(
    [str(project_root / 'agentnexus/__main__.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    # collect_submodules('chromadb'): chromadb resolves several implementations
    # via string config + importlib (e.g. chromadb.telemetry.product.posthog),
    # invisible to PyInstaller's static analysis — bundle them all.
    hiddenimports=[
        'chromadb',
        'sentence_transformers',
        'jieba',
        'pymupdf',
        'rank_bm25',
        'mcp',
        'mcp.client.stdio',
        'mcp.client.streamable_http',
        'openai',
        'tiktoken',
        'tiktoken_ext',
        'tiktoken_ext.openai_public',
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
    ] + collect_submodules('chromadb'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Chroma receives our SentenceTransformer embeddings for add and query.
    # Its optional default ONNX embedding runtime is not used by the app.
    excludes=['onnxruntime'],
    noarchive=False,
    optimize=0,
)

if sys.platform == 'win32':
    # PATH may contain older copies shipped with Java or other SDKs. Use a
    # consistent system C++ runtime for Torch and the Python bootloader.
    import pefile

    runtime_names = {'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'}
    system_dir = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'
    collected_names = {name.lower() for name, _, _ in a.binaries}
    if not runtime_names <= collected_names:
        raise RuntimeError(f'Missing collected C++ runtime DLLs: {sorted(runtime_names - collected_names)}')

    for name in sorted(runtime_names):
        source = system_dir / name
        if not source.is_file():
            raise RuntimeError(f'Missing C++ runtime in System32: {source}')
        image = pefile.PE(str(source), fast_load=True)
        try:
            image.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
            fixed_info = image.VS_FIXEDFILEINFO[0]
            version = (fixed_info.FileVersionMS >> 16, fixed_info.FileVersionMS & 0xFFFF)
        finally:
            image.close()
        if version < (14, 51):
            raise RuntimeError(f'{source} is version {version}; this package requires the tested 14.51+ C++ runtime')

    a.binaries = [
        (name, str(system_dir / name), kind) if name.lower() in runtime_names else (name, source, kind)
        for name, source, kind in a.binaries
    ]

pyz = PYZ(a.pure)
# Onedir, not onefile: a single-file build forces the bootloader to extract
# ~2GB (torch DLLs etc.) into %TEMP%\_MEI on EVERY launch — measured ~11.5s
# of a 12.7s cold start — and leaked _MEI dirs orphan the backend process.
# Onedir loads DLLs in place: cold start drops to ~2-3s.
exe = EXE(
    pyz,
    a.scripts,
    [],
    [],
    exclude_binaries=True,
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='agentnexus',
)
