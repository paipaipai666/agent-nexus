# Prepare knowledge base models

The desktop installer excludes model weights. The default embedding model needs about 92 MiB on first download. The optional reranker needs about 2.14 GiB.

## Start using the knowledge base

1. Connect to the internet, open **Settings → Knowledge**, and upload a document. The first upload downloads the embedding model.
2. Wait for ingestion to finish before searching. The page shows the current stage while downloading.
3. To enable reranking, click **Download model**. Until it is ready, results identify their hybrid ranking mode.

If downloading fails, check your connection and free disk space. Click **Retry preparation**, then upload the failed document again. The page also provides a preparation action when an existing index needs rebuilding.

## Prepare for offline use

With the CLI, prepare the embedding model while online:

```bash
nexus kb prepare
```

To prepare the reranker too, run:

```bash
nexus kb prepare --reranker
```

After preparation, cached models work offline. Offline ingestion fails explicitly if the embedding model is missing, without writing hash fallback vectors.

Default models use `HF_HUB_CACHE/agentnexus/<repository>/<pinned revision>`. Complete existing Hugging Face caches also work. When moving to an offline machine, retain both the model cache and AgentNexus application data.

If `HF_HUB_OFFLINE=1` or `TRANSFORMERS_OFFLINE=1` is set, unset it before preparing missing models.
