# Food Image-to-Recipe Retrieval

Given a photo of food, return the most relevant recipes from a database. This is a
**retrieval** problem, not generation: the model does not write recipes, it ranks
existing ones by similarity in a shared image-text embedding space.

MSc ML course final project.

---

## Results

Measured on a 3,000-recipe database, both directions. Retrieval is asymmetric, so
reporting only the better direction would overstate the system.

| Direction | R@1 | R@5 | R@10 | median rank |
|---|---|---|---|---|
| image to recipe | 22.4% | 48.8% | **61.0%** | **6** |
| recipe to image | 18.7% | 43.3% | 55.4% | 8 |

Random baseline on 3,000 recipes is a median rank of 1,500. Raw numbers in
[`outputs/retrieval_results.csv`](outputs/retrieval_results.csv).

### Fusion weight ablation

A recipe has two useful text views: the full text (title, ingredients, instructions)
and the ingredients alone. Ingredients correlate more directly with what is visible
in the photo. Blending the two embeddings with weight alpha, and sweeping alpha:

| alpha | R@1 | R@5 | R@10 |
|---|---|---|---|
| 0.3 | 13.4% | 32.1% | 41.9% |
| 0.5 | 17.5% | 41.0% | 51.6% |
| 0.7 | 20.7% | 46.5% | 57.9% |
| **0.9** | **22.4%** | **48.8%** | **61.0%** |

Recall@10 moves 19 points across the sweep, so this is not a parameter worth guessing
at. alpha = 0.9 was selected on measured Recall@K. Raw numbers in
[`outputs/fusion_comparison.csv`](outputs/fusion_comparison.csv).

---

## Two systems in this repo

They are separate. Their numbers are not comparable and should not be merged.

### 1. Zero-shot retrieval (the results above)

Both CLIP encoders, no training. CLIP was pretrained on 400M image-text pairs, so
food images and recipe text already land near each other in its 512-d space.

```
Food image  -> CLIP vision encoder -> 512-d, L2-normalised -.
                                                            |-- inner product -> rank
Recipe text -> CLIP text encoder   -> 512-d, L2-normalised -'
```

FAISS inner-product search at inference. Embeddings precomputed once.

### 2. Trained dual-encoder (`src/`)

Learns its own joint space instead of borrowing CLIP's, so recipe text is not capped
at CLIP's 77-token limit.

- **Frozen CLIP ViT-B/32** for images. Features are precomputed once for all 13,582
  images and cached to disk. Running CLIP per batch would repeat the same forward pass
  every epoch, so caching cuts training time by roughly 10x.
- **DistilBERT** for recipe text, not CLIP's text encoder: CLIP caps at 77 tokens and
  recipe instructions average 200+.
- **Separate ingredient and instruction streams.** Ingredients are an unordered list,
  instructions are sequential. Keeping them apart lets the fusion module weight them
  differently.
- **Symmetric InfoNCE**, temperature 0.07, computed image-to-recipe and recipe-to-image.
- **Three fusion modes**, switchable by config: `concat`, `cross-attention`, and
  `ingr_only` as an ablation.
- Projection to a shared 1024-d space, L2-normalised on both sides.

Reported result for this variant: median rank 10 of 200 validation recipes, on a
2,000-recipe run over 6 epochs. That is a smaller experiment than the zero-shot
evaluation above and the two are not interchangeable.

---

## Layout

```
src/
  data/     kaggle_adapter.py, precompute_image_feats.py, build_dataset.py
  models/   image_encoder.py, text_encoder.py, fusion.py, joint_embedding.py
  losses/   infonce.py
  eval/     metrics.py (medR + R@k, both directions), evaluate.py, demo.py
  training/ train.py (AMP, gradient accumulation, early stopping, TensorBoard)
  utils/    config.py (OmegaConf), seed.py
configs/    baseline.yaml, fusion.yaml, data.yaml
tests/      58 tests across data, model, loss and eval
outputs/    measured results (csv + plots)
```

All hyperparameters live in `configs/`. Nothing is hardcoded in source, and any value
can be overridden from the command line, so a run is reproducible from its config.

## Tests

```bash
pytest            # 58 tests
```

## Running it

```bash
pip install -r requirements.txt

# 1. cache CLIP image features (once)
python -m src.data.precompute_image_feats --config baseline.yaml

# 2. train
python -m src.training.train --config baseline.yaml train.batch_size=32

# 3. evaluate a checkpoint
python -m src.eval.evaluate --config baseline.yaml --checkpoint runs/baseline_concat/best.pt

# 4. query with one image
python -m src.eval.demo --config baseline.yaml \
  --checkpoint runs/baseline_concat/best.pt --image path/to/food.jpg --topk 5

# 5. training curves
tensorboard --logdir runs/
```

## Data

Food Ingredients and Recipe Dataset with Image Name Mapping (Kaggle): 13,501 recipes,
13,582 images, 13,471 matched pairs. Not committed to this repo. Download it and point
`configs/data.yaml` at your local copy.

## What is hard about this

- Images and text start in unrelated feature spaces; the whole job is closing that gap.
- Many dishes look alike. Pasta, salads and soups are visually near-degenerate.
- Ingredient lists have no meaningful order, so pooling over them must be order-invariant.
- Retrieval over 10k+ recipes needs approximate search to stay interactive.

## Further reading

- [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md) - design decisions for the trained model
- [`PROJECT_OVERVIEW_NOTEBOOK.md`](PROJECT_OVERVIEW_NOTEBOOK.md) - the zero-shot pipeline
