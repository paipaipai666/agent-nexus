"""Judge calibration eval CLI command."""

import json
from pathlib import Path

import typer

from agentnexus.cli import console, eval_app
from agentnexus.cli.eval.stats import _compute_calibration
from agentnexus.rag.ingestion import ChunkStrategy


def _rag_evaluator_cls():
    from agentnexus.cli import eval_cmd

    return getattr(eval_cmd, "RAGEvaluator")


@eval_app.command("calibrate")
def eval_calibrate(
    output: str = typer.Option("./calibrate_samples.json", "--output", "-o", help="输出文件路径"),
    score_file: str = typer.Option(
        "",
        "--score-file",
        "-s",
        help="人工评分 JSON 文件路径（含 human_precision/human_recall 字段）",
    ),
):
    """Judge 校准：导出样本供人工打分，计算 Judge 与人工评分的一致性"""
    from agentnexus.rag.eval_dataset import EVAL_SAMPLES, KNOWLEDGE_BASE
    from agentnexus.rag.retriever import HybridRetriever, build_knowledge_base
    from agentnexus.storage.chroma import delete_collection

    evaluator = _rag_evaluator_cls()(KNOWLEDGE_BASE, EVAL_SAMPLES)
    strategy, chunk_size, overlap, use_hybrid = ChunkStrategy.FIXED, 256, 64, False

    console.print(f"[bold]校准运行: {strategy.value}-{chunk_size}-dense[/bold]\n")

    samples = []
    chunks = evaluator._chunk_all(strategy, chunk_size, overlap)

    delete_collection(namespace="eval")
    build_knowledge_base(chunks, load_reranker=False, namespace="eval")

    retriever = HybridRetriever(namespace="eval")
    retriever.rebuild_from_catalog()

    for idx, sample in enumerate(EVAL_SAMPLES):
        _, retrieved = evaluator._retrieve(
            sample.question,
            retriever,
            use_hybrid,
            max_tokens=max(len(sample.question) * 5, 100),
        )
        if not retrieved:
            samples.append({
                "sample_idx": idx,
                "question": sample.question,
                "ground_truth": sample.ground_truth,
                "is_negative": not sample.ground_truth,
                "retrieved": [],
                "judge_precision": 0.0,
                "judge_recall": 0.0,
                "judge_faithfulness": 0.0,
                "judge_relevancy": 0.0,
                "human_precision": None,
                "human_recall": None,
            })
            continue

        answer = evaluator._generate_answer(sample.question, retrieved)
        judge_precision = evaluator._score_precision(sample, retrieved)
        judge_recall = evaluator._score_recall(sample, retrieved)
        judge_faithfulness = evaluator._score_faithfulness(answer, retrieved)
        judge_relevancy = (
            evaluator._score_correctness(sample.question, answer, sample.ground_truth)
            if sample.ground_truth
            else 0.0
        )

        samples.append({
            "sample_idx": idx,
            "question": sample.question,
            "ground_truth": sample.ground_truth,
            "is_negative": not sample.ground_truth,
            "retrieved": retrieved[:3],
            "judge_precision": round(judge_precision, 4),
            "judge_recall": round(judge_recall, 4),
            "judge_faithfulness": round(judge_faithfulness, 4),
            "judge_relevancy": round(judge_relevancy, 4),
            "human_precision": None,
            "human_recall": None,
        })

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(samples, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if score_file:
        _compute_calibration(samples, score_file)
    else:
        console.print(
            f"[green]已导出 {len(samples)} 个校准样本到: {output_path}[/green]"
        )
        console.print(
            "\n填写每个样本的 [bold]human_precision[/bold] 和 "
            "[bold]human_recall[/bold] (0.0~1.0)，然后重新运行:"
        )
        console.print(f"  [dim]nexus eval calibrate --score-file {output_path}[/dim]")


