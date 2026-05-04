import os
import re
from pathlib import Path

import fitz


SUPPORTED_EXTENSIONS = frozenset({".pdf", ".md", ".txt"})


def _extract_pdf(file_path: str) -> str:
    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return f"[PDF 解析失败: {e}]"
    blocks = []
    for page in doc:
        for block in page.get_text("blocks"):
            if block[6] == 0:  # block_type=0 表示文本块
                blocks.append(block[4])
    doc.close()
    return "\n".join(blocks)


def _extract_md(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # 去掉代码块（避免把 ``` 当成内容）
    md_text = re.sub(r"```[\s\S]*?```", "", md_text)
    # 去掉图片 ![alt](url)
    md_text = re.sub(r"!\[.*?\]\(.*?\)", "", md_text)
    # 链接 → 保留文字 [text](url) → text
    md_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md_text)
    # 去掉标题标记 # ## ### 等
    md_text = re.sub(r"^#{1,6}\s+", "", md_text, flags=re.MULTILINE)
    # 去掉加粗/斜体
    md_text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", md_text)
    # 去掉行内代码 `code`
    md_text = re.sub(r"`([^`]+)`", r"\1", md_text)
    # 去掉无序列表标记 - * +
    md_text = re.sub(r"^[\s]*[-*+]\s+", "", md_text, flags=re.MULTILINE)
    # 去掉有序列表标记 1. 2.
    md_text = re.sub(r"^[\s]*\d+\.\s+", "", md_text, flags=re.MULTILINE)
    # 去掉引用 > 
    md_text = re.sub(r"^>\s?", "", md_text, flags=re.MULTILINE)
    # 去掉水平线 --- ***
    md_text = re.sub(r"^[-*_]{3,}\s*$", "", md_text, flags=re.MULTILINE)
    return md_text


def _extract_txt(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def load_document(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {ext}，支持: {SUPPORTED_EXTENSIONS}")

    if ext == ".pdf":
        return _extract_pdf(file_path)
    if ext == ".md":
        return _extract_md(file_path)
    return _extract_txt(file_path)


def clean_text(text: str) -> str:
    # 去掉控制字符（保留 \n \t）
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # 全角数字/字母 → 半角
    text = text.translate(str.maketrans(
        "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    ))
    # 合并连续空行（3+ → 2个）
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 每行去首尾空格
    text = "\n".join(line.strip() for line in text.split("\n"))
    # PDF 断行合并：连续两行都是中文内容时合并
    text = re.sub(r"(?<=[^\x00-\x7f])\n(?=[^\x00-\x7f])", "", text)
    # 去掉短于 3 字符且不含中文标点的行
    text = "\n".join(
        line for line in text.split("\n")
        if len(line) >= 3 or re.search(r"[，。！？、；：""''（）]", line)
    )
    # 去空白行两端的空行
    return text.strip()


from enum import Enum
from langchain_text_splitters import RecursiveCharacterTextSplitter

from agentnexus.prompts import load_prompt

CONTEXTUAL_PROMPT = load_prompt("contextual")


class ChunkStrategy(Enum):
    FIXED = "fixed"
    RECURSIVE = "recursive"
    SEMANTIC = "semantic"


_SEPARATORS = [
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    "，",
    ".",
    "!",
    "?",
    ";",
    ",",
    " ",
    "",
]


def chunk_text(
    text: str,
    strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> list[str]:
    if strategy == ChunkStrategy.FIXED:
        return _fixed_window_split(text, chunk_size, chunk_overlap)
    if strategy == ChunkStrategy.RECURSIVE:
        return _recursive_split(text, chunk_size, chunk_overlap)
    if strategy == ChunkStrategy.SEMANTIC:
        return _semantic_split(text, chunk_size, chunk_overlap)
    raise ValueError(f"未知的分块策略: {strategy}")


def _fixed_window_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    step = chunk_size - overlap
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


def _recursive_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        separators=_SEPARATORS,
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        length_function=len,
        is_separator_regex=False,
    )
    return [doc.strip() for doc in splitter.split_text(text) if doc.strip()]


def _semantic_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    # 第一轮：按双换行拆分成段落
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks = []
    buffer = paragraphs[0]

    for para in paragraphs[1:]:
        if len(buffer) + len(para) <= chunk_size:
            buffer += "\n\n" + para
        else:
            if len(buffer) > chunk_size:
                # 超大段落降级为递归分块
                chunks.extend(_recursive_split(buffer, chunk_size, overlap))
            else:
                chunks.append(buffer)
            buffer = para

    if len(buffer) > chunk_size:
        chunks.extend(_recursive_split(buffer, chunk_size, overlap))
    else:
        chunks.append(buffer)

    # 添加 overlap：每个 chunk 末尾截取 overlap 字符拼到下一个 chunk 前面
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prefix = chunks[i - 1][-overlap:].strip()
            overlapped.append(prefix + "\n" + chunks[i] if prefix else chunks[i])
        return overlapped

    return chunks


def load_and_clean(file_path: str) -> str:
    return clean_text(load_document(file_path))


def ingest(
    file_path: str,
    strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    enable_contextual: bool = False,
    llm_client=None,
) -> list[str]:
    text = load_and_clean(file_path)
    chunks = chunk_text(text, strategy=strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    if enable_contextual and llm_client and chunks:
        chunks = enrich_chunks_with_context(chunks, text, llm_client)

    return chunks


def generate_chunk_context(document: str, chunk: str, llm_client) -> str:
    prompt = CONTEXTUAL_PROMPT.format(document=document, chunk=chunk)
    try:
        response = llm_client.think(
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            silent=True,
        )
        return response.strip() if response else ""
    except Exception:
        return ""


def enrich_chunks_with_context(
    chunks: list[str],
    document: str,
    llm_client,
) -> list[str]:
    from rich.progress import track
    enriched = []
    for chunk in track(chunks, description="生成上下文摘要..."):
        context = generate_chunk_context(document, chunk, llm_client)
        if context:
            enriched.append(f"{context}\n\n{chunk}")
        else:
            enriched.append(chunk)
    return enriched
