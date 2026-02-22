import os
from typing import Optional
from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger
import inspect
from functools import wraps


        #do full preprocessing here no matter what, the decision to wether or not preprocess should come from llm reasonning
        # then save the preprocessed data and use that for downstream tasks

def unwrap_llm_dict_call(func):
    """
    Allow LLM/tool runtimes that pass a single dict as the first positional arg
    (or inside kwargs under the first param name) to be expanded into named args.
    - Preserves defaults.
    - Treats '' and None as 'unspecified' → use default.
    - Ignores extra keys not in the function signature.
    - Passes through any extra kwargs (e.g., context_variables) untouched.
    """
    sig = inspect.signature(func)
    param_names = [p for p in sig.parameters.keys()]  # includes 'self' for methods

    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Identify if we were given a dict "positionally"
        dict_positional = (len(args) > 1 and isinstance(args[1], dict))
        # Or a dict nested under the first non-self param via kwargs
        first_nonself = next((p for p in param_names if p != "self"), None)
        dict_in_kwargs = (
            first_nonself in kwargs and isinstance(kwargs[first_nonself], dict)
        )

        if dict_positional or dict_in_kwargs:
            # Start by binding 'self' (if any) and any non-dict kwargs already provided
            if dict_positional:
                params = args[1]
                bound = sig.bind_partial(*args[:1])  # bind self only
            else:
                params = kwargs.pop(first_nonself)  # remove the dict from kwargs
                bound = sig.bind_partial(*args)     # bind whatever was passed

            # Fill signature parameters from the dict or defaults
            for name, param in sig.parameters.items():
                if name == "self":
                    continue
                # If already set via kwargs (non-dict), leave it
                if name in bound.arguments:
                    continue
                # Pick value from params or default
                v = params.get(name, param.default) if isinstance(params, dict) else param.default
                # Treat empty string / None as "use default"
                if v in ("", None) and param.default is not inspect._empty:
                    v = param.default
                # Only set if value is not "no default"
                if v is not inspect._empty:
                    bound.arguments[name] = v

            # Now merge back any remaining kwargs (e.g., context_variables)
            for k, v in kwargs.items():
                if k not in bound.arguments:
                    bound.arguments[k] = v

            return await func(*bound.args, **bound.kwargs)

        # Not a dict-style call → pass through
        return await func(*args, **kwargs)

    return wrapper


class GenePanelToolSet(ToolSet):
    """
    Gene panel selection toolset (HVG-only first version).
    Args:
        name (str): Name of the toolset.
        default_adata_path (Optional[str]): Default path to the .h5ad dataset.
        default_workdir (str): Default working directory to save results.
    """

    def __init__(
        self,
        name: str = "gene_panel_selection",
        default_adata_path: Optional[str] = None,
        default_workdir: str = ".",
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.default_adata_path = default_adata_path
        self.default_workdir = default_workdir
        logger.info(
            f"GenePanelToolSet initialized (name={name}, default_adata_path={default_adata_path}, default_workdir={default_workdir})"
        )
        
    def _coerce(self, value, default, cast):
        """
        Convert values safely.
        - If value is None, empty string, or 'none' => return default.
        - Otherwise cast(value) (e.g., int, float).
        """
        if value is None:
            return default
        if isinstance(value, str) and value.strip().lower() in ("", "none", "null"):
            return default
        try:
            return cast(value)
        except:
            return default

    # ---------------------------------------------------------------------- #
    #  CellTypist - Train an annotator model
    #  pip install celltypist
    # ---------------------------------------------------------------------- #
#     @tool
#     @unwrap_llm_dict_call
#     async def train_celltypist_annotator(
#         self,
#         adata_path: Optional[str] = None,
#         label_key: str = "",
#         model_name: str = "celltypist_model",
#         n_jobs: str = "10",
#         feature_selection: str = "true",
#         ensure_log1p: str = "true",
#         score_method: str = "maxabs",     # "maxabs" | "l2"
#         return_scores: str = "false",
#         save_meta: str = "true",
#         workdir: Optional[str] = None,
#     ) -> dict:
#         """
#         Train a CellTypist annotator and export a score file.

#         Args:
#             adata_path (str): Path to `.h5ad`. If None, uses default dataset.
#             label_key (str): Column in `adata.obs` containing ground-truth labels (e.g., "cell_type").
#             model_name (str): Output model name (without extension).
#             n_jobs (str): Number of CPU jobs for training.
#             feature_selection (str): "true" to enable CellTypist feature selection.
#             ensure_log1p (str): "true" to apply normalize_total + log1p if data looks like raw counts.
#             score_method (str): Aggregate multiclass weights into a single gene score:
#                                 - "maxabs": max_c |w_{c,g}|
#                                 - "l2": ||w_{.,g}||_2
#             return_scores (str): "true" -> return [{gene, score}] else return only file paths.
#             save_meta (str): "true" to save a tiny metadata file.
#             workdir (str): Directory to save results (default = default_workdir).

#         Returns:
#             dict: {
#                 "used_dataset": str,
#                 "label_key": str,
#                 "n_cells": int,
#                 "n_genes_input": int,
#                 "n_genes_model": int,
#                 "score_method": str,
#                 "saved_to": {
#                     "model": str,
#                     "scores": str,
#                     "notes": str or None
#                 },
#                 "genes": List[dict] (only if return_scores=true)
#             }
#         """
#         try:
#             import os, time
#             import scanpy as sc
#             import numpy as np
#             import pandas as pd
#             import celltypist
#             import joblib

#             logger.info("=" * 60)
#             logger.info("CELLTYPIST TRAIN - START")
#             logger.info("=" * 60)

#             # --- Resolve dataset path ---
#             path = adata_path or self.default_adata_path
#             if not path:
#                 return {"error": "No dataset provided and no default_adata_path defined."}
#             if not label_key:
#                 return {"error": "label_key is required (must be a column name in adata.obs)."}

#             # --- Resolve workdir + output folder ---
#             workdir = workdir or self.default_workdir
#             out_dir = os.path.join(workdir, "gene_panels", "celltypist")
#             os.makedirs(out_dir, exist_ok=True)

#             # --- Parse params ---
#             n_jobs = self._coerce(n_jobs, 10, int)
#             feature_selection = str(feature_selection).lower() in ("true", "1", "yes")
#             ensure_log1p = str(ensure_log1p).lower() in ("true", "1", "yes")
#             return_scores = str(return_scores).lower() in ("true", "1", "yes")
#             save_meta = str(save_meta).lower() in ("true", "1", "yes")
#             score_method = (score_method or "maxabs").strip().lower()
#             if score_method not in ("maxabs", "l2"):
#                 return {"error": f"score_method must be 'maxabs' or 'l2' (got '{score_method}')."}

#             logger.info(
#                 f"Parameters: model_name={model_name}, n_jobs={n_jobs}, "
#                 f"feature_selection={feature_selection}, ensure_log1p={ensure_log1p}, "
#                 f"score_method={score_method}, return_scores={return_scores}"
#             )

#             # --- Load dataset ---
#             logger.info("STEP 1: Loading h5ad file...")
#             t0 = time.time()
#             adata = sc.read_h5ad(path)
#             logger.info(f"✓ Loaded in {time.time() - t0:.2f}s - Shape: {adata.shape}")

#             if getattr(adata, "isbacked", False):
#                 logger.info("⚠️  Dataset is in 'backed' mode, converting to memory...")
#                 adata = adata.to_memory()
#                 logger.info("✓ Converted to memory mode")

#             if label_key not in adata.obs.columns:
#                 return {"error": f"label_key '{label_key}' not found in adata.obs."}

#             n_genes_input = int(adata.n_vars)

#             # --- Optional preprocessing guard ---
#             logger.info("STEP 2: Optional preprocessing checks...")
#             if ensure_log1p:
#                 looks_like_counts = False
#                 try:
#                     Xmax = float(adata.X[: min(1000, adata.n_obs)].max())
#                     looks_like_counts = (Xmax > 50) and float(Xmax).is_integer()
#                 except Exception:
#                     pass

#                 if looks_like_counts:
#                     logger.info("  → Data looks like raw counts -> normalize_total + log1p")
#                     sc.pp.normalize_total(adata, target_sum=1e4)
#                     sc.pp.log1p(adata)
#                     logger.info("  ✓ Applied normalize_total + log1p")
#                 else:
#                     logger.info("  ✓ Skipping normalize/log1p (data does not look like raw counts)")

#             # --- Train model ---
#             logger.info("STEP 3: Training CellTypist model...")
#             t0 = time.time()
#             model = celltypist.train(
#                 adata,
#                 labels=label_key,
#                 n_jobs=n_jobs,
#                 feature_selection=feature_selection,
#             )
#             logger.info(f"✓ Training completed in {time.time() - t0:.2f}s")

#             # --- Save model ---
#             logger.info("STEP 4: Saving model...")
#             model_path = os.path.join(out_dir, f"{model_name}.pkl")
#             model.write(model_path)
#             logger.info(f"✓ Saved model to: {model_path}")

#             # --- Reload + extract scores (stable across versions) ---
#             logger.info("STEP 5: Extracting gene scores...")
#             d = joblib.load(model_path)
#             clf = d.get("Model", None)
#             if clf is None or not hasattr(clf, "coef_") or not hasattr(clf, "features"):
#                 return {"error": "Saved model.pkl does not contain expected Model.coef_ and Model.features"}

#             genes = list(clf.features)
#             W = np.asarray(clf.coef_, dtype=float)
#             if W.ndim == 1:
#                 W = W.reshape(1, -1)
#             if W.shape[1] != len(genes):
#                 return {"error": f"Shape mismatch: coef_.shape={W.shape} vs len(features)={len(genes)}"}

#             if score_method == "maxabs":
#                 scores = np.max(np.abs(W), axis=0)
#             else:
#                 scores = np.linalg.norm(W, axis=0)

#             score_df = (pd.DataFrame({"gene": genes, "score": scores})
#                         .sort_values("score", ascending=False)
#                         .reset_index(drop=True))

#             n_genes_model = int(score_df.shape[0])

#             # --- Save ONLY scores.csv ---
#             logger.info("STEP 6: Saving scores file ...")
#             scores_path = os.path.join(out_dir, f"{model_name}_scores.csv")
#             score_df.to_csv(scores_path, index=False)
#             logger.info(f"✓ Saved scores to: {scores_path}")

#             # --- Optional meta ---
#             notes_path = None
#             if save_meta:
#                 notes_path = os.path.join(out_dir, f"{model_name}_meta.txt")
#                 with open(notes_path, "w") as f:
#                     f.write(f"used_dataset: {path}\n")
#                     f.write(f"label_key: {label_key}\n")
#                     f.write(f"n_cells: {int(adata.n_obs)}\n")
#                     f.write(f"n_genes_input: {n_genes_input}\n")
#                     f.write(f"n_genes_model: {n_genes_model}\n")
#                     f.write(f"feature_selection: {feature_selection}\n")
#                     f.write(f"ensure_log1p: {ensure_log1p}\n")
#                     f.write(f"score_method: {score_method}\n")
#                 logger.info(f"✓ Saved metadata to: {notes_path}")

#             logger.info("=" * 60)
#             logger.info("CELLTYPIST TRAIN - COMPLETED SUCCESSFULLY")
#             logger.info("=" * 60)

#             return {
#                 "used_dataset": path,
#                 "label_key": label_key,
#                 "n_cells": int(adata.n_obs),
#                 "n_genes_input": n_genes_input,
#                 "n_genes_model": n_genes_model,
#                 "score_method": score_method,
#                 "saved_to": {
#                     "model": model_path,
#                     "scores": scores_path,
#                     "notes": notes_path,
#                 },
#                 "genes": score_df.to_dict(orient="records") if return_scores else [],
#             }

#         except Exception as e:
#             import traceback
#             logger.error("=" * 60)
#             logger.error("CELLTYPIST TRAIN - FAILED")
#             logger.error(f"Error: {str(e)}")
#             logger.error(f"Error type: {type(e).__name__}")
#             logger.error(f"Traceback:\n{traceback.format_exc()}")
#             logger.error("=" * 60)
#             return {"error": str(e)}

# # ------
# # annotate adata (CellTypist)
# # ------

#     @tool
#     @unwrap_llm_dict_call
#     async def annotate_celltypes_celltypist(
#         self,
#         adata_path: Optional[str] = None,
#         model_path: str = "",                  # trained CellTypist .pkl
#         over_clustering_key: str = "leiden",   # default = leiden (kept as argument)
#         prediction_key: str = "celltypist_label",
#         out_labels_name: str = "celltypist_labels.csv",
#         workdir: Optional[str] = None,
#     ) -> dict:
#         """Annotate cell types in a dataset using a trained CellTypist model.

#         This tool:
#         - loads a `.h5ad` dataset
#         - runs CellTypist annotation with majority voting enabled
#         - uses an existing over-clustering (e.g. Leiden) for voting
#         - saves the final predicted cell-type labels to a CSV file

#         Args:
#             adata_path (str): Path to the `.h5ad` dataset. If None, uses default dataset.
#             model_path (str): Path to a trained CellTypist `.pkl` model.
#             over_clustering_key (str): Column in `adata.obs` used for over-clustering
#                                     during majority voting (default: "leiden").
#             prediction_key (str): Name of the column storing predicted labels in the output CSV.
#             out_labels_name (str): Filename of the saved labels CSV.
#             workdir (str): Directory to save results (default = default_workdir).

#         Returns:
#             dict: {
#                 "used_dataset": str,
#                 "model_used": str,
#                 "prediction_key": str,
#                 "over_clustering_key": str,
#                 "n_cells": int,
#                 "saved_to": {
#                     "labels": str,
#                     "meta": str
#                 }
#             }
#         """
#         try:
#             import os, time
#             import scanpy as sc
#             import pandas as pd
#             import celltypist

#             logger.info("=" * 60)
#             logger.info("CELLTYPIST ANNOTATE - START")
#             logger.info("=" * 60)

#             # --- Resolve paths ---
#             path = adata_path or getattr(self, "default_adata_path", None)
#             if not path:
#                 return {"error": "No adata_path provided (and no default_adata_path set)."}
#             if not model_path or not os.path.exists(model_path):
#                 return {"error": f"Invalid model_path: {model_path}"}

#             workdir = workdir or getattr(self, "default_workdir", ".")
#             out_dir = os.path.join(workdir, "gene_panels", "celltypist")
#             os.makedirs(out_dir, exist_ok=True)

#             logger.info(f"Dataset: {path}")
#             logger.info(f"Model: {model_path}")
#             logger.info(f"majority_voting=True, over_clustering_key='{over_clustering_key}'")

#             # --- Load adata ---
#             logger.info("STEP 1: Loading adata...")
#             adata = sc.read_h5ad(path)
#             if getattr(adata, "isbacked", False):
#                 adata = adata.to_memory()
#             logger.info(f"✓ Loaded adata: {adata.shape}")

#             # --- Validate over-clustering key ---
# # --- Ensure over-clustering exists (compute Leiden if missing) ---
#             if over_clustering_key not in adata.obs.columns:
#                 logger.info(
#                     f"over_clustering_key '{over_clustering_key}' not found → computing Leiden clustering"
#                 )

#                 import scanpy as sc

#                 # Minimal, robust defaults
#                 if "X_pca" not in adata.obsm:
#                     sc.pp.pca(adata, n_comps=50)

#                 if "neighbors" not in adata.uns:
#                     sc.pp.neighbors(adata, n_neighbors=15)

#                 sc.tl.leiden(
#                     adata,
#                     resolution=1.0,
#                     key_added=over_clustering_key,
#                 )

#                 logger.info(
#                     f"✓ Computed Leiden clustering stored in adata.obs['{over_clustering_key}']"
#                 )
      
#             # --- Annotate (majority voting always ON) ---
#             logger.info("STEP 2: Running CellTypist...")
#             t0 = time.time()
#             preds = celltypist.annotate(
#                 adata,
#                 model=model_path,
#                 majority_voting=True,
#                 over_clustering=over_clustering_key,
#             )
#             logger.info(f"✓ Annotation done in {time.time() - t0:.2f}s")

#             # --- Extract FINAL labels (1D, safe) ---
#             if not hasattr(preds, "predicted_labels"):
#                 return {"error": "CellTypist output missing 'predicted_labels'."}

#             df = preds.predicted_labels
#             if "majority_voting" not in df.columns:
#                 return {"error": "'majority_voting' column missing from CellTypist output."}

#             labels = (
#                 pd.Series(df["majority_voting"], index=adata.obs_names)
#                 .astype(str)
#                 .values
#             )

#             # --- Save labels CSV ---
#             labels_path = os.path.join(out_dir, out_labels_name)
#             pd.DataFrame(
#                 {
#                     "cell_id": adata.obs_names,
#                     prediction_key: labels,
#                 }
#             ).to_csv(labels_path, index=False)

#             # --- Save minimal metadata (traceability) ---
#             meta_path = labels_path.replace(".csv", "_meta.txt")
#             with open(meta_path, "w") as f:
#                 f.write(f"adata_path: {path}\n")
#                 f.write(f"model_path: {model_path}\n")
#                 f.write(f"over_clustering_key: {over_clustering_key}\n")
#                 f.write("majority_voting: true\n")
#                 f.write("label_source: majority_voting\n")
#                 f.write(f"prediction_key: {prediction_key}\n")
#                 f.write(f"n_cells: {adata.n_obs}\n")

#             logger.info(f"✓ Saved labels to: {labels_path}")
#             logger.info(f"✓ Saved metadata to: {meta_path}")

#             logger.info("=" * 60)
#             logger.info("CELLTYPIST ANNOTATE - COMPLETED SUCCESSFULLY")
#             logger.info("=" * 60)

#             return {
#                 "used_dataset": path,
#                 "model_used": model_path,
#                 "prediction_key": prediction_key,
#                 "label_source": "majority_voting",
#                 "over_clustering_key": over_clustering_key,
#                 "n_cells": int(adata.n_obs),
#                 "saved_to": {
#                     "labels": labels_path,
#                     "meta": meta_path,
#                 },
#             }

#         except Exception as e:
#             import traceback
#             logger.error("CELLTYPIST ANNOTATE - FAILED")
#             logger.error(traceback.format_exc())
#             return {"error": str(e)}



    
    #---------------------------------------------------------------------------
    # SpaPROS
    #---------------------------------------------------------------------------
    @tool
    @unwrap_llm_dict_call
    async def select_spapros(
        self,
        adata_path: Optional[str] = None,
        label_key: str = "",
        num_markers: str = "100",
        n_hvg: str = "3000",  # always use n_hvg lower than 3000
        return_scores: str = "false",
        workdir: Optional[str] = None,
    ) -> dict:
        """
        Select marker genes using SpaPROS.

        Args:
            adata_path (str): Path to `.h5ad`. If None, uses default dataset.
            label_key (str): Column in `.obs` to use as groups.
            num_markers (str): Number of marker genes to select.
            n_hvg (str): Number of highly-variable genes to consider must always be below 3000.
            return_scores (str): "true" → return [{gene, score}], otherwise return gene list.
            workdir (str): Directory to save results (default = default_workdir).

        Returns:
            dict: {
                "used_dataset": str,
                "top_n": int,
                "saved_to": dict,
                "genes": List[str] or List[dict]
            }
        """

        try:
            import scanpy as sc
            import pandas as pd
            import spapros as sp
            import numpy as np
            import os

            # ---- unwrap dict calls from Pantheon ----
            if isinstance(adata_path, dict):
                params = adata_path
                adata_path   = params.get("adata_path", self.default_adata_path)
                label_key    = params.get("label_key", label_key)
                num_markers  = params.get("num_markers", num_markers)
                n_hvg        = params.get("n_hvg", n_hvg)
                return_scores = params.get("return_scores", return_scores)
                workdir      = params.get("workdir", workdir)

            # ---- Resolve dataset ----
            path = adata_path or self.default_adata_path
            if not path:
                return {"error": "No dataset provided and no default_adata_path defined."}

            workdir = workdir or self.default_workdir
            out_dir = os.path.join(workdir, "gene_panels", "spapros")
            os.makedirs(out_dir, exist_ok=True)

            # ---- Parse types ----
            num_markers  = self._coerce(num_markers, 100, int)
            n_hvg        = self._coerce(n_hvg, 3000, int)
            return_scores = str(return_scores).lower() in ("true", "yes", "1")

            # ---- Load dataset ----
            adata = sc.read_h5ad(path)

            # ---- HVG selection ----
            sc.pp.highly_variable_genes(adata, flavor="cell_ranger", n_top_genes=n_hvg)
            adata = adata[:, adata.var["highly_variable"]]

            # ---- Check labels ----
            if not label_key or label_key not in adata.obs.columns:
                # Auto Leiden if no label
                sc.pp.normalize_total(adata)
                sc.pp.log1p(adata)
                sc.pp.pca(adata)
                sc.pp.neighbors(adata)
                sc.tl.leiden(adata, resolution=1.0, key_added="leiden_auto")
                label_key = "leiden_auto"

            # ---- Run SpaPROS ----
            selector = sp.se.ProbesetSelector(
                adata,
                n=num_markers,
                celltype_key=label_key,
                verbosity=1,
                save_dir=None
            )
            selector.select_probeset()

            df = selector.probeset.copy()
            df.index.name = "gene"

            # ------------------------------------------------------------------
            # SAVE OUTPUTS
            # ------------------------------------------------------------------
            # Save full table (all genes with metrics)
            full_path = os.path.join(out_dir, "spapros_full_table.csv")
            df.to_csv(full_path)

            # Extract final selected markers
            selected = df[df["selection"] == True].index.tolist()

            panel_path = os.path.join(out_dir, f"spapros_top_{num_markers}.csv")
            pd.DataFrame({"gene": selected}).to_csv(panel_path, index=False)

            # ------------------------------------------------------------------
            # RETURN DICT
            # ------------------------------------------------------------------
            if return_scores:
                score_list = []
                if "importance_score" in df.columns:
                    for g, row in df.iterrows():
                        score_list.append({
                            "gene": g,
                            "score": float(row.get("importance_score", np.nan))
                        })

                score_path = os.path.join(out_dir, "spapros_scores.csv")
                pd.DataFrame(score_list).to_csv(score_path, index=False)

                return {
                    "used_dataset": path,
                    "top_n": num_markers,
                    "saved_to": {
                        "panel": panel_path,
                        "full_table": full_path,
                        "scores": score_path,
                    },
                    "genes": score_list,
                }

            # ---- Return only gene list ----
            return {
                "used_dataset": path,
                "top_n": num_markers,
                "saved_to": {
                    "panel": panel_path,
                    "full_table": full_path,
                },
                "genes": selected,
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": f"SpaPROS failed: {e}"}


        
    #---------------------------------------------------------------------------
    #Random Forest Gene Selection
    #--------------------------------------------------------------------------#
    @tool
    @unwrap_llm_dict_call
    async def select_random_forest(
    self,
    adata_path: Optional[str] = None,
    label_key: str = "",
    n_top_genes: str = "1000",
    return_scores: str = "false",
    random_state: str = "42",
    workdir: Optional[str] = None,
) -> dict:
        """
        Select informative genes using Random Forest feature importance.

        Args:
            adata_path (str): Path to .h5ad dataset (if None -> default_adata_path).
            label_key (str): Column in `.obs` to use. If missing → auto Leiden clustering.
            n_top_genes (str): Number of genes to return (default "1000").
            return_scores (str): "true" → return [{gene, score}], else return top genes only.
            random_state (str): Random seed for the RandomForest.
            workdir (str): Where to save results (default = default_workdir).

        Returns:
            dict {
                used_dataset: str
                top_n: int
                saved_to: str
                genes: list[str] or list[{gene, score}]
            }
        """
        try:
            import scanpy as sc
            import numpy as np
            import pandas as pd
            from sklearn.ensemble import RandomForestClassifier

            # ---- Dict call unwrap (Pantheon agent call case) ----
            if isinstance(adata_path, dict):
                params = adata_path
                print("[DEBUG] Unwrapping dict call:", params, flush=True)
                adata_path   = params.get("adata_path", self.default_adata_path)
                label_key    = params.get("label_key", label_key)
                n_top_genes  = params.get("n_top_genes", n_top_genes)
                return_scores = params.get("return_scores", return_scores)
                random_state = params.get("random_state", random_state)
                workdir      = params.get("workdir", workdir)

            # ---- Resolve dataset and workdir ----
            path = adata_path or self.default_adata_path
            if not path:
                return {"error": "No dataset provided and no default_adata_path defined."}

            workdir = workdir or self.default_workdir
            out_dir = os.path.join(workdir, "gene_panels", "random_forest")
            os.makedirs(out_dir, exist_ok=True)

            # ---- Type conversion (safe via _coerce) ----
            n_top_genes   = self._coerce(n_top_genes, 1000, int)
            random_state  = self._coerce(random_state, 42, int)
            return_scores = (str(return_scores).lower() in ("true", "1", "yes"))

            # ---- Load dataset ----
            adata = sc.read_h5ad(path)

            # ---- Ensure labeling ----
            if not label_key or label_key not in adata.obs.columns:
                # sc.pp.normalize_total(adata)
                # sc.pp.log1p(adata)
                # sc.pp.pca(adata)
                # sc.pp.neighbors(adata)
                # sc.tl.leiden(adata, key_added="leiden_auto")
                label_key = "leiden_auto"

            # ---- Train Random Forest ----
            X = adata.X.toarray() if not isinstance(adata.X, np.ndarray) else adata.X
            y = adata.obs[label_key].astype("category").cat.codes.values

            clf = RandomForestClassifier(n_estimators=300, random_state=random_state, n_jobs=-1)
            clf.fit(X, y)

            scores = clf.feature_importances_
            ordered = sorted(
                [{"gene": g, "score": float(s)} for g, s in zip(adata.var_names, scores)],
                key=lambda d: d["score"], reverse=True
            )

            save_path = os.path.join(out_dir, f"rf_top_{n_top_genes}.csv")
            pd.DataFrame(ordered[:n_top_genes]).to_csv(save_path, index=False)

            return {
                "used_dataset": path,
                "top_n": n_top_genes,
                "saved_to": save_path,
                "genes": ordered if return_scores else [x["gene"] for x in ordered[:n_top_genes]],
            }

        except Exception as e:
            print(adata_path)
            logger.error(f"select_random_forest failed: {e}")
            return {"error": str(e)}





        #--ScgeneFit--#
    # ---------------------------------------------------------------------- #
    #  scGeneFit
    #pip install git+https://github.com/solevillar/scGeneFit-python.git
    # ---------------------------------------------------------------------- #
    @tool
    @unwrap_llm_dict_call
    async def select_scgenefit(
        self,
        adata_path: Optional[str] = None,
        label_key: Optional[str] = None,
        n_top_genes: str = "200",
        method: str = "centers",          # "centers" | "pairwise" | "pairwise_centers"
        epsilon_param: str = "1.0",
        sampling_rate: str = "1.0",
        n_neighbors: str = "3",
        max_constraints: str = "1000",  # always <=1000
        redundancy: str = "0.01",
        return_scores: str = "false",     # must be string like HVG tool
        workdir: Optional[str] = None,
    ) -> dict:
        """
        Select marker genes using scGeneFit (LP-based marker selection).

        Args:
            adata_path (str): Path to `.h5ad`. If None, uses default dataset.
            label_key (str): Column in `.obs` to use as groups. If missing, Leiden clustering is run.
            n_top_genes (str): Number of markers to select.
            method (str): Constraint-building strategy ("centers" | "pairwise" | "pairwise_centers").
            epsilon_param (str): Scaling of epsilon, default 1.0.
            sampling_rate (str): Fraction of cells to sample (pairwise methods).
            n_neighbors (str): Neighbors for pairwise constraint mode.
            max_constraints (str): Maximum constraint rows, this should ALWAYS be below 1000.
            redundancy (str): Redundancy parameter for centers summarization.
            return_scores (str): "true" → return [{gene, score}], otherwise return top gene list.
            workdir (str): Directory to save results (default = default_workdir).

        Returns:
            dict: {
                "used_dataset": str,
                "top_n": int,
                "saved_to": str,
                "genes": List[str] or List[dict]
            }
        """
        try:
            import scanpy as sc
            import numpy as np
            import pandas as pd
            import scipy.sparse as sp
            import scGeneFit.functions as gf
            import time

            logger.info("=" * 60)
            logger.info("SCGENEFIT - START")
            logger.info("=" * 60)

            # --- If Pantheon passed params as dict, unwrap exactly like HVG ---
            if isinstance(adata_path, dict):
                params = adata_path
                logger.info(f"[DEBUG] Unwrapping dict call: {params}")
                adata_path      = params.get("adata_path", self.default_adata_path)
                label_key       = params.get("label_key", label_key)
                n_top_genes     = params.get("n_top_genes", n_top_genes)
                method          = params.get("method", method)
                epsilon_param   = params.get("epsilon_param", epsilon_param)
                sampling_rate   = params.get("sampling_rate", sampling_rate)
                n_neighbors     = params.get("n_neighbors", n_neighbors)
                max_constraints = params.get("max_constraints", max_constraints)
                redundancy      = params.get("redundancy", redundancy)
                return_scores   = params.get("return_scores", return_scores)
                workdir         = params.get("workdir", workdir)

            # --- Resolve dataset path ---
            path = adata_path or self.default_adata_path
            if not path:
                return {"error": "No dataset provided and no default_adata_path defined."}

            logger.info(f"Dataset path: {path}")

            # --- Resolve workdir + create output folder ---
            workdir = workdir or self.default_workdir
            out_dir = os.path.join(workdir, "gene_panels", "scgenefit")
            os.makedirs(out_dir, exist_ok=True)
            
            # --- SAFE Conversion for every numeric param ---
            n_top_genes     = self._coerce(n_top_genes, 200, int)
            epsilon_param   = self._coerce(epsilon_param, 1.0, float)
            sampling_rate   = self._coerce(sampling_rate, 1.0, float)
            n_neighbors     = self._coerce(n_neighbors, 3, int)
            max_constraints = self._coerce(max_constraints, 1000, int)
            redundancy      = self._coerce(redundancy, 0.01, float)
            return_scores   = (str(return_scores).lower() in ("true", "1", "yes"))

            logger.info(f"Parameters: n_top_genes={n_top_genes}, method={method}, epsilon={epsilon_param}")

            # --- Load dataset ---
            logger.info("STEP 1: Loading h5ad file...")
            start_time = time.time()
            adata = sc.read_h5ad(path)
            logger.info(f"✓ Loaded in {time.time() - start_time:.2f}s - Shape: {adata.shape}")
            
            # Check if backed mode
            if adata.isbacked:
                logger.info("⚠️  Dataset is in 'backed' mode, converting to memory...")
                adata = adata.to_memory()
                logger.info("✓ Converted to memory mode")

            # Auto clustering if label_key missing
            if not label_key or label_key not in adata.obs.columns:
                logger.info(f"⚠️  label_key '{label_key}' not found, using 'leiden_auto'")
                # sc.pp.normalize_total(adata, target_sum=1e4)
                # sc.pp.log1p(adata)
                # sc.pp.pca(adata, n_comps=30)
                # sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)
                # sc.tl.leiden(adata, key_added="leiden_auto")
                label_key = "leiden_auto"

            # --- Detailed matrix diagnostics ---
            logger.info("-" * 60)
            logger.info("STEP 2: Matrix diagnostics")
            logger.info(f"Matrix type: {type(adata.X)}")
            logger.info(f"Matrix shape: {adata.X.shape}")
            logger.info(f"Matrix dtype: {adata.X.dtype}")
            logger.info(f"Is sparse: {sp.issparse(adata.X)}")
            
            if sp.issparse(adata.X):
                logger.info(f"Sparse format: {adata.X.format}")
                density = adata.X.nnz / (adata.X.shape[0] * adata.X.shape[1]) * 100
                logger.info(f"Density: {density:.4f}%")
                logger.info(f"Non-zero values: {adata.X.nnz:,}")
                sparse_size_mb = (adata.X.data.nbytes + adata.X.indices.nbytes + adata.X.indptr.nbytes) / 1e6
                dense_size_gb = adata.X.shape[0] * adata.X.shape[1] * 8 / 1e9  # float64
                logger.info(f"Current sparse size: {sparse_size_mb:.2f} MB")
                logger.info(f"Dense size would be: {dense_size_gb:.2f} GB (float64)")
            
            # --- Convert to dense array ---
            logger.info("-" * 60)
            logger.info("STEP 3: Converting sparse matrix to dense array...")
            logger.info("⏳ Starting .toarray() - this is where it might be slow...")
            
            start_time = time.time()
            if not isinstance(adata.X, np.ndarray):
                # Test on small subset first
                logger.info("  → Testing on first 100 rows...")
                test_start = time.time()
                _ = adata.X[:100].toarray()
                test_time = time.time() - test_start
                logger.info(f"  → 100 rows took {test_time:.3f}s")
                
                estimated_total = test_time * (adata.X.shape[0] / 100)
                logger.info(f"  → Estimated total time: {estimated_total:.1f}s ({estimated_total/60:.1f} minutes)")
                
                # Full conversion
                logger.info("  → Converting full matrix...")
                X = adata.X.toarray()
            else:
                logger.info("  → Matrix is already dense, no conversion needed")
                X = adata.X
            
            elapsed = time.time() - start_time
            logger.info(f"✓ Conversion completed in {elapsed:.2f}s ({elapsed/60:.2f} minutes)")
            logger.info(f"  Result shape: {X.shape}, dtype: {X.dtype}")
            
            # --- Extract labels ---
            logger.info("-" * 60)
            logger.info("STEP 4: Extracting labels...")
            start_time = time.time()
            y = adata.obs[label_key].astype("category").values
            d = X.shape[1]
            unique_labels = np.unique(y)
            logger.info(f"✓ Labels extracted in {time.time() - start_time:.3f}s")
            logger.info(f"  Number of samples: {len(y)}")
            logger.info(f"  Number of unique labels: {len(unique_labels)}")
            logger.info(f"  Label distribution: {dict(zip(*np.unique(y, return_counts=True)))}")

            # --- Internal scGeneFit functions ---
            logger.info("-" * 60)
            logger.info("STEP 5: Running scGeneFit algorithm...")
            _sample        = getattr(gf, "__sample")
            _pairwise      = getattr(gf, "__select_constraints_pairwise")
            _pairwise_cent = getattr(gf, "__select_constraints_centers")
            _summarized    = getattr(gf, "__select_constraints_summarized")
            _lp_markers    = getattr(gf, "__lp_markers")

            logger.info(f"  → Sampling with rate={sampling_rate}...")
            start_time = time.time()
            samples, samples_labels, _ = _sample(X, y, sampling_rate)
            logger.info(f"  ✓ Sampling done in {time.time() - start_time:.2f}s - {len(samples)} samples")

            logger.info(f"  → Building constraints with method='{method}'...")
            start_time = time.time()
            if method == "pairwise_centers":
                constraints, smallest_norm = _pairwise_cent(X, y, samples, samples_labels)
            elif method == "pairwise":
                constraints, smallest_norm = _pairwise(X, y, samples, samples_labels, n_neighbors)
            else:
                constraints, smallest_norm = _summarized(X, y, redundancy)
            logger.info(f"  ✓ Constraints built in {time.time() - start_time:.2f}s")
            logger.info(f"    Constraint matrix shape: {constraints.shape}")
            logger.info(f"    Smallest norm: {smallest_norm:.6f}")

            # Cap constraints
            if constraints.shape[0] > max_constraints:
                logger.info(f"  → Capping constraints from {constraints.shape[0]} to {max_constraints}...")
                constraints = constraints[np.random.permutation(constraints.shape[0])[:max_constraints], :]
                logger.info(f"  ✓ Constraints capped")
            
            # Solve LP
            logger.info(f"  → Solving LP with {constraints.shape[1]} variables and {constraints.shape[0]} constraints...")
            start_time = time.time()
            sol = _lp_markers(constraints, n_top_genes, smallest_norm * epsilon_param)
            logger.info(f"  ✓ LP solved in {time.time() - start_time:.2f}s")
            
            weights = np.asarray(sol["x"][:d], dtype=float)
            n_selected = np.sum(weights > 0)
            logger.info(f"  ✓ Selected {n_selected} markers with non-zero weights")

            # ---- Return with scores ----
            logger.info("-" * 60)
            logger.info("STEP 6: Preparing results...")
            if return_scores:
                ranked = sorted(
                    [{"gene": g, "score": float(s)} for g, s in zip(adata.var_names, weights)],
                    key=lambda d: d["score"],
                    reverse=True,
                )
                save_path = os.path.join(out_dir, "scgenefit_scores.csv")
                pd.DataFrame(ranked).to_csv(save_path, index=False)
                logger.info(f"✓ Saved scores to: {save_path}")

                return {
                    "used_dataset": path,
                    "top_n": len(ranked),
                    "saved_to": save_path,
                    "genes": ranked
                }

            # ---- Return only top genes ----
            order = np.argsort(-weights)[:n_top_genes]
            top = adata.var_names[order].tolist()
            save_path = os.path.join(out_dir, f"scgenefit_top_{n_top_genes}.csv")
            pd.DataFrame({"gene": top}).to_csv(save_path, index=False)
            logger.info(f"✓ Saved top {n_top_genes} genes to: {save_path}")
            logger.info(f"✓ Top 10 genes: {top[:10]}")

            logger.info("=" * 60)
            logger.info("SCGENEFIT - COMPLETED SUCCESSFULLY")
            logger.info("=" * 60)

            return {
                "used_dataset": path,
                "top_n": n_top_genes,
                "saved_to": save_path,
                "genes": top
            }

        except Exception as e:
            logger.error("=" * 60)
            logger.error(f"SCGENEFIT - FAILED")
            logger.error(f"Error: {str(e)}")
            logger.error(f"Error type: {type(e).__name__}")
            import traceback
            logger.error(f"Traceback:\n{traceback.format_exc()}")
            logger.error("=" * 60)
            return {"error": str(e)}
    
    #-- Highly Variable Genes (HVG) Selection --#
    # @tool
    # @unwrap_llm_dict_call
    # async def select_hvg(
    #     self,
    #     adata_path: str | None = None,
    #     n_top_genes: str = "1000",
    #     layer: str = "",
    #     return_scores: str = "false",
    #     workdir: str | None = None,
    # ) -> dict:
    #     """
    #     Select highly variable genes (HVG) from a .h5ad dataset and save results.
        
    #     Args: 
    #         adata_path (str): Path to the .h5ad dataset. If None, uses default_adata_path.
    #         n_top_genes (str): Number of top HVGs to select. Default is "1000".
    #         layer (str): Optional layer name in AnnData to use for HVG selection.
    #         return_scores (str): Whether to return gene scores along with gene names. Default is "false".
    #         workdir (str): Working directory to save results. If None, uses default_workdir.
    #     returns:
    #         dict: {
    #             "used_dataset": str,
    #             "top_n": int,
    #             "saved_to": str,
    #             "genes": List[str] or List[dict] (if return_scores is true)
    #         }
    #     """
    #     try:
    #         import scanpy as sc
    #         import pandas as pd
    #         # --- If Pantheon sent a dictionary, unwrap it ---
    #         if isinstance(adata_path, dict):
    #             params = adata_path
    #             print("[DEBUG] Unwrapping dict call:", params, flush=True)
    #             adata_path = params.get("adata_path", self.default_adata_path)
    #             n_top_genes = params.get("n_top_genes", n_top_genes)
    #             layer = params.get("layer", layer)
    #             return_scores = params.get("return_scores", return_scores)
    #             workdir = params.get("workdir", workdir)

    #         # Resolve dataset path
    #         path = adata_path or self.default_adata_path
    #         if not path:
    #             return {"error": "No dataset provided and no default_adata_path defined."}

    #         # Resolve workdir
    #         workdir = workdir or self.default_workdir
    #         os.makedirs(os.path.join(workdir, "gene_panels"), exist_ok=True)

    #         # Convert inputs
    #         n_top_genes   = self._coerce(n_top_genes, 1000, int)
    #         return_scores = (str(return_scores).lower() in ("true", "1", "yes"))

    #         # Load
    #         adata = sc.read_h5ad(path)

    #         # HVG
    #         sc.pp.highly_variable_genes(adata, n_top_genes=n_top_genes, layer=layer or None)

    #         genes = list(adata.var_names)
    #         scores = adata.var["dispersions_norm"].tolist()

    #         ordered = sorted(
    #             [{"gene": g, "score": s} for g, s in zip(genes, scores)],
    #             key=lambda d: d["score"],
    #             reverse=True,
    #         )

    #         # Save
    #         save_path = os.path.join(workdir, "gene_panels", f"hvg_top_{n_top_genes}.csv")
    #         pd.DataFrame(ordered[:n_top_genes]).to_csv(save_path, index=False)

    #         return {
    #             "used_dataset": path,
    #             "top_n": n_top_genes,
    #             "saved_to": save_path,
    #             "genes": ordered if return_scores else [x["gene"] for x in ordered[:n_top_genes]],
    #         }

    #     except Exception as e:
    #         print(adata_path)
    #         logger.error(f"select_hvg failed: {e}")
    #         return {"error": str(e)}

    # @tool
    # @unwrap_llm_dict_call
    # async def select_differential_expression(
    #     self,
    #     adata_path: Optional[str] = None,
    #     label_key: str = "",
    #     n_top_genes: str = "1000",
    #     resolution: str = "1.0",
    #     reference: str = "rest",
    #     return_scores: str = "false",
    #     collapse: str = "true",
    #     workdir: Optional[str] = None,
    # ) -> dict:
    #     """
    #     Differential expression using Scanpy's rank_genes_groups (Wilcoxon test).

    #     Args:
    #         adata_path: Path to .h5ad dataset. If None, uses default_adata_path.
    #         label_key: Column in adata.obs with group labels. If empty, Leiden clustering is computed.
    #         n_top_genes: Number of DE genes to return (if not returning scores).
    #         resolution: Leiden resolution (if computing clusters).
    #         reference: Reference group (default: "rest").
    #         return_scores: "true" to return score table, "false" to return gene lists.
    #         collapse: "true" → collapse to one score per gene; "false" → keep cluster-gene rows.
    #         workdir: Folder to store results.

    #     Returns:
    #         dict containing dataset used, where results saved, and selected genes.
    #     """
    #     try:
    #         import scanpy as sc
    #         import numpy as np
    #         import pandas as pd

    #         # ---- Handle dict input (Pantheon wrapped call) ----
    #         if isinstance(adata_path, dict):
    #             params = adata_path
    #             print("[DEBUG] Unwrapping dict call:", params, flush=True)
    #             adata_path = params.get("adata_path", self.default_adata_path)
    #             label_key = params.get("label_key", label_key)
    #             n_top_genes = params.get("n_top_genes", n_top_genes)
    #             resolution = params.get("resolution", resolution)
    #             reference = params.get("reference", reference)
    #             return_scores = params.get("return_scores", return_scores)
    #             collapse = params.get("collapse", collapse)
    #             workdir = params.get("workdir", workdir)

    #         # ---- Resolve paths ----
    #         path = adata_path or self.default_adata_path
    #         if not path:
    #             return {"error": "No dataset provided and no default_adata_path defined."}

    #         workdir = workdir or self.default_workdir
    #         os.makedirs(os.path.join(workdir, "gene_panels"), exist_ok=True)

    #         # ---- Convert types ----
    #         n_top_genes = self._coerce(n_top_genes, 1000, int)
    #         resolution   = self._coerce(resolution, 1.0, float)

    #         return_scores = str(return_scores).lower() in ("true", "1", "yes")
    #         collapse      = str(collapse).lower() in ("true", "1", "yes")

    #         # ---- Load dataset ----
    #         adata = sc.read_h5ad(path)

    #         # ---- Determine group labels ----
    #         if label_key and label_key in adata.obs.columns:
    #             groupby_key = label_key
    #         else:
    #             # sc.pp.pca(adata, n_comps=50)
    #             # sc.pp.neighbors(adata, n_neighbors=15, n_pcs=40)
    #             # sc.tl.leiden(adata, resolution=resolution, key_added="leiden_auto")
    #             groupby_key = "leiden_auto"

    #         # ---- Differential expression ----
    #         sc.tl.rank_genes_groups(adata, groupby=groupby_key, reference=reference, method="wilcoxon")

    #         names = adata.uns["rank_genes_groups"]["names"]
    #         lfc_raw = adata.uns["rank_genes_groups"]["logfoldchanges"]
    #         clusters = names.dtype.names
    #         scores = {cl: np.abs(np.array(lfc_raw[cl], dtype=float)) for cl in clusters}

    #         # ---- If returning score table ----
    #         if return_scores:
    #             rows = []
    #             for cl in clusters:
    #                 for g, s in zip(names[cl], scores[cl]):
    #                     rows.append({"gene": g, "cluster": cl, "score": float(s)})
    #             df = pd.DataFrame(rows)

    #             if collapse:
    #                 df = df.groupby("gene", as_index=False)["score"].max().sort_values("score", ascending=False)

    #             save_path = os.path.join(workdir, "gene_panels", f"de_scores_{groupby_key}.csv")
    #             df.to_csv(save_path, index=False)

    #             return {
    #                 "used_dataset": path,
    #                 "grouping": groupby_key,
    #                 "scores_saved_to": save_path,
    #                 "genes": df.to_dict(orient="records"),
    #             }

    #         # ---- Otherwise: return top genes per cluster ----
    #         result = {cl: list(names[cl][:n_top_genes]) for cl in clusters}
    #         save_path = os.path.join(workdir, "gene_panels", f"de_clusters_{groupby_key}_top_{n_top_genes}.csv")

    #         pd.DataFrame(
    #             [{"cluster": cl, "genes": result[cl]} for cl in clusters]
    #         ).to_csv(save_path, index=False)

    #         return {
    #             "used_dataset": path,
    #             "grouping": groupby_key,
    #             "saved_to": save_path,
    #             "genes": result,
    #         }

    #     except Exception as e:
    #         logger.error(f"select_differential_expression failed: {e}")
    #         return {"error": str(e)}
        
    # @tool
    # @unwrap_llm_dict_call
    # async def evaluate_gene_panel(
    #     self,
    #     adata_paths: str,        # comma-separated list of .h5ad paths
    #     panel_path: str,         # CSV containing 'gene' column
    #     label_key: str = "cell_type",
    #     workdir: str | None = None,
    # ) -> dict:
    #     """
    #     Evaluate a gene panel on one or more test datasets.

    #     Args:
    #         adata_paths (str): Comma-separated paths to .h5ad test datasets.
    #         panel_path (str): Path to CSV containing a 'gene' column.
    #         label_key (str): Column name in `.obs` indicating true cell types.
    #         workdir (str): Directory where results and plots are saved.

    #     Returns:
    #         dict {
    #             "panel_size_total": int,         # total genes in panel file
    #             "evaluations": list[dict],       # one per test dataset
    #             "boxplot_path": str              # saved image path
    #         }
    #     """
    #     import os, pandas as pd, scanpy as sc, matplotlib.pyplot as plt, seaborn as sns
    #     from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score, pairwise_distances
    #     import numpy as np

    #     workdir = workdir or self.default_workdir
    #     os.makedirs(workdir, exist_ok=True)

    #     # --- Helper functions ---
    #     def separation_index(adata, label_key="leiden"):
    #         X = adata.obsm["X_pca"]
    #         labels = adata.obs[label_key].values
    #         dist = pairwise_distances(X, metric="euclidean")
    #         intra, inter = [], []
    #         for g in np.unique(labels):
    #             idx = np.where(labels == g)[0]
    #             jdx = np.where(labels != g)[0]
    #             if len(idx) > 1:
    #                 intra.append(dist[np.ix_(idx, idx)].mean())
    #             inter.append(dist[np.ix_(idx, jdx)].mean())
    #         return np.nan if len(intra) == 0 else np.mean(inter) / np.mean(intra)

    #     def evaluate_panel_single(adata, genes, label_key="cell_type", resolution=0.8, n_neighbors=15, n_pcs=50):
    #         genes_present = [g for g in genes if g in adata.var_names]
    #         ad = adata[:, genes_present].copy()
    #         sc.pp.pca(ad, n_comps=n_pcs)
    #         sc.pp.neighbors(ad, n_neighbors=n_neighbors, use_rep="X_pca")
    #         sc.tl.leiden(ad, resolution=resolution, seed=0)

    #         clusters = ad.obs["leiden"]
    #         true = ad.obs[label_key]
    #         ari = adjusted_rand_score(true, clusters)
    #         nmi = normalized_mutual_info_score(true, clusters)
    #         si = separation_index(ad, label_key="leiden")
    #         return dict(
    #             ARI=ari,
    #             NMI=nmi,
    #             SI=si,
    #             panel_size_used=len(genes_present),
    #             panel_coverage=f"{len(genes_present)}/{len(genes)}",
    #         )

    #     # --- Main evaluation ---
    #     genes = pd.read_csv(panel_path)["gene"].dropna().unique().tolist()
    #     adata_paths = [p.strip() for p in adata_paths.split(",") if p.strip()]
    #     results = []
    #     for path in adata_paths:
    #         adata = sc.read_h5ad(path)
    #         metrics = evaluate_panel_single(adata, genes, label_key=label_key)
    #         metrics["dataset"] = os.path.basename(path)
    #         results.append(metrics)

    #     df = pd.DataFrame(results)

    #     # --- Visualization ---
    #     plt.figure(figsize=(10, 5))
    #     df_melt = df.melt(id_vars="dataset", value_vars=["NMI", "ARI", "SI"],
    #                     var_name="Metric", value_name="Score")
    #     sns.boxplot(x="Metric", y="Score", data=df_melt, color="lightgray")
    #     sns.stripplot(x="Metric", y="Score", data=df_melt, hue="dataset",
    #                 dodge=True, marker="o", size=6)
    #     plt.title("Gene Panel Evaluation Across Test Datasets")
    #     plt.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
    #     out_path = os.path.join(workdir, "gene_panel_evaluation_boxplots.png")
    #     plt.tight_layout()
    #     plt.savefig(out_path)
    #     plt.close()

    #     return {
    #         "panel_size_total": len(genes),
    #         "evaluations": results,
    #         "boxplot_path": out_path,
    #     }



__all__ = ["GenePanelToolSet"]
