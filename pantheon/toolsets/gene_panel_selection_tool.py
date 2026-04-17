import os
import inspect
from typing import Optional
from functools import wraps

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger


def unwrap_llm_dict_call(func):
    """
    Decorator that transparently expands a single dict argument into named
    keyword arguments.  This handles the common case where an LLM tool-call
    runtime passes all parameters packed inside one dictionary.

    Behaviour:
      - Preserves defaults declared in the function signature.
      - Treats '' and None as "unspecified" -> falls back to the default.
      - Ignores extra keys not present in the signature.
      - Passes through extra kwargs (e.g. context_variables) untouched.
    """
    sig = inspect.signature(func)
    param_names = list(sig.parameters.keys())

    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Detect if a dict was passed as the first positional arg (after self)
        dict_positional = len(args) > 1 and isinstance(args[1], dict)

        # Or nested under the first non-self parameter name via kwargs
        first_nonself = next((p for p in param_names if p != "self"), None)
        dict_in_kwargs = (
            first_nonself in kwargs and isinstance(kwargs[first_nonself], dict)
        )

        if dict_positional or dict_in_kwargs:
            if dict_positional:
                params = args[1]
                bound = sig.bind_partial(*args[:1])
            else:
                params = kwargs.pop(first_nonself)
                bound = sig.bind_partial(*args)

            for name, param in sig.parameters.items():
                if name == "self" or name in bound.arguments:
                    continue
                v = params.get(name, param.default) if isinstance(params, dict) else param.default
                if v in ("", None) and param.default is not inspect._empty:
                    v = param.default
                if v is not inspect._empty:
                    bound.arguments[name] = v

            for k, v in kwargs.items():
                if k not in bound.arguments:
                    bound.arguments[k] = v

            return await func(*bound.args, **bound.kwargs)

        return await func(*args, **kwargs)

    return wrapper


class GenePanelToolSet(ToolSet):
    """Toolset for algorithmic gene panel selection methods.

    Exposes SpaPROS, Random Forest, and scGeneFit as async tool calls.
    HVG and DE are intentionally handled by the LLM in the notebook
    (via scanpy) since they are simple one-liners.

    Args:
        name:               Toolset name registered with the agent runtime.
        default_adata_path: Fallback .h5ad path when the caller omits one.
        default_workdir:    Fallback directory for saving outputs.
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
            f"GenePanelToolSet initialized "
            f"(name={name}, adata={default_adata_path}, workdir={default_workdir})"
        )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _resolve(self, adata_path, workdir):
        """Return (path, workdir) after applying defaults."""
        path = adata_path or self.default_adata_path
        wdir = workdir or self.default_workdir
        return path, wdir

    # ------------------------------------------------------------------ #
    #  SpaPROS                                                             #
    # ------------------------------------------------------------------ #

    @tool
    @unwrap_llm_dict_call
    async def select_spapros(
        self,
        adata_path: Optional[str] = None,
        label_key: str = "",
        num_markers: int = 100,
        n_hvg: int = 3000,
        return_scores: bool = False,
        workdir: Optional[str] = None,
    ) -> dict:
        """
        Select marker genes using SpaPROS probe-set selection.

        Args:
            adata_path (str): Path to .h5ad dataset. Falls back to default.
            label_key (str): Column in .obs for cell groups.
            num_markers (int): Number of markers to select.
            n_hvg (int): HVG pre-filter size (must be < 3000).
            return_scores (bool): Include per-gene importance scores.
            workdir (str): Output directory. Falls back to default.

        Returns:
            dict with keys: used_dataset, top_n, saved_to, genes.
        """
        try:
            import scanpy as sc
            import pandas as pd
            import numpy as np
            import spapros as sp
        except ImportError as e:
            return {
                "error": (
                    "SpaPROS requires scanpy, pandas, numpy, spapros. "
                    "Install with: pip install scanpy spapros. "
                    f"Details: {e}"
                )
            }

        try:
            path, workdir = self._resolve(adata_path, workdir)
            if not path:
                return {"error": "No dataset path provided."}

            out_dir = os.path.join(workdir, "gene_panels", "spapros")
            os.makedirs(out_dir, exist_ok=True)

            adata = sc.read_h5ad(path)

            # Pre-filter to HVGs for tractable computation
            sc.pp.highly_variable_genes(adata, flavor="cell_ranger", n_top_genes=n_hvg)
            adata = adata[:, adata.var["highly_variable"]]

            if not label_key or label_key not in adata.obs.columns:
                return {"error": f"label_key '{label_key}' not found in adata.obs."}

            selector = sp.se.ProbesetSelector(
                adata,
                n=num_markers,
                celltype_key=label_key,
                verbosity=1,
                save_dir=None,
            )
            selector.select_probeset()

            df = selector.probeset.copy()
            df.index.name = "gene"

            # Save full results table
            full_path = os.path.join(out_dir, "spapros_full_table.csv")
            df.to_csv(full_path)

            selected = df[df["selection"] == True].index.tolist()
            panel_path = os.path.join(out_dir, f"spapros_top_{num_markers}.csv")
            pd.DataFrame({"gene": selected}).to_csv(panel_path, index=False)

            if return_scores and "importance_score" in df.columns:
                score_list = [
                    {"gene": g, "score": float(row.get("importance_score", np.nan))}
                    for g, row in df.iterrows()
                ]
                score_path = os.path.join(out_dir, "spapros_scores.csv")
                pd.DataFrame(score_list).to_csv(score_path, index=False)
                return {
                    "used_dataset": path,
                    "top_n": num_markers,
                    "saved_to": {"panel": panel_path, "full_table": full_path, "scores": score_path},
                    "genes": score_list,
                }

            return {
                "used_dataset": path,
                "top_n": num_markers,
                "saved_to": {"panel": panel_path, "full_table": full_path},
                "genes": selected,
            }

        except Exception as e:
            import traceback
            logger.error(f"select_spapros failed: {e}\n{traceback.format_exc()}")
            return {"error": f"SpaPROS failed: {e}"}

    # ------------------------------------------------------------------ #
    #  Random Forest                                                       #
    # ------------------------------------------------------------------ #

    @tool
    @unwrap_llm_dict_call
    async def select_random_forest(
        self,
        adata_path: Optional[str] = None,
        label_key: str = "",
        n_top_genes: int = 1000,
        return_scores: bool = False,
        random_state: int = 42,
        workdir: Optional[str] = None,
    ) -> dict:
        """
        Rank genes by Random Forest feature importance.

        Trains an RF classifier on the expression matrix (X) to predict
        cell labels, then returns genes ranked by Gini importance.

        Args:
            adata_path (str): Path to .h5ad dataset. Falls back to default.
            label_key (str): Column in .obs for cell labels.
            n_top_genes (int): How many top genes to save (default 1000).
            return_scores (bool): Return all genes with scores.
            random_state (int): Random seed.
            workdir (str): Output directory. Falls back to default.

        Returns:
            dict with keys: used_dataset, top_n, saved_to, genes.
        """
        try:
            import scanpy as sc
            import numpy as np
            import pandas as pd
            from sklearn.ensemble import RandomForestClassifier
        except ImportError as e:
            return {
                "error": (
                    "Random Forest selection requires scanpy, numpy, pandas, scikit-learn. "
                    "Install with: pip install scanpy scikit-learn. "
                    f"Details: {e}"
                )
            }

        try:
            path, workdir = self._resolve(adata_path, workdir)
            if not path:
                return {"error": "No dataset path provided."}

            out_dir = os.path.join(workdir, "gene_panels", "random_forest")
            os.makedirs(out_dir, exist_ok=True)

            adata = sc.read_h5ad(path)

            if not label_key or label_key not in adata.obs.columns:
                return {"error": f"label_key '{label_key}' not found in adata.obs."}

            X = adata.X.toarray() if not isinstance(adata.X, np.ndarray) else adata.X
            y = adata.obs[label_key].astype("category").cat.codes.values

            clf = RandomForestClassifier(
                n_estimators=300, random_state=random_state, n_jobs=-1
            )
            clf.fit(X, y)

            ranked = sorted(
                [{"gene": g, "score": float(s)} for g, s in zip(adata.var_names, clf.feature_importances_)],
                key=lambda d: d["score"],
                reverse=True,
            )

            save_path = os.path.join(out_dir, f"rf_top_{n_top_genes}.csv")
            pd.DataFrame(ranked[:n_top_genes]).to_csv(save_path, index=False)

            return {
                "used_dataset": path,
                "top_n": n_top_genes,
                "saved_to": save_path,
                "genes": ranked if return_scores else [x["gene"] for x in ranked[:n_top_genes]],
            }

        except Exception as e:
            logger.error(f"select_random_forest failed: {e}")
            return {"error": str(e)}

    # ------------------------------------------------------------------ #
    #  scGeneFit                                                           #
    # ------------------------------------------------------------------ #

    @tool
    @unwrap_llm_dict_call
    async def select_scgenefit(
        self,
        adata_path: Optional[str] = None,
        label_key: Optional[str] = None,
        n_top_genes: int = 200,
        method: str = "centers",
        epsilon_param: float = 1.0,
        sampling_rate: float = 1.0,
        n_neighbors: int = 3,
        max_constraints: int = 1000,
        redundancy: float = 0.01,
        return_scores: bool = False,
        workdir: Optional[str] = None,
    ) -> dict:
        """
        Select marker genes via scGeneFit (LP-based marker selection).

        Solves a linear program that finds a sparse weight vector over genes
        such that labeled cell groups remain separable.  Weights serve as
        per-gene importance scores.

        Args:
            adata_path (str): Path to .h5ad. Falls back to default.
            label_key (str): Column in .obs for cell labels.
            n_top_genes (int): Number of markers to select (default 200).
            method (str): Constraint strategy: "centers" | "pairwise" | "pairwise_centers".
            epsilon_param (float): LP epsilon scaling factor (default 1.0).
            sampling_rate (float): Fraction of cells to sample for pairwise methods.
            n_neighbors (int): Neighbours for pairwise constraint building.
            max_constraints (int): Hard cap on constraint rows (keep <= 1000).
            redundancy (float): Redundancy param for center summarisation.
            return_scores (bool): Return all genes with LP weights.
            workdir (str): Output directory. Falls back to default.

        Returns:
            dict with keys: used_dataset, top_n, saved_to, genes.
        """
        try:
            import scanpy as sc
            import numpy as np
            import pandas as pd
            import scipy.sparse as sps
            import scGeneFit.functions as gf
        except ImportError as e:
            return {
                "error": (
                    "scGeneFit selection requires scanpy, numpy, pandas, scipy, scGeneFit. "
                    "Install with: pip install scanpy scGeneFit. "
                    f"Details: {e}"
                )
            }

        try:
            import time

            path, workdir = self._resolve(adata_path, workdir)
            if not path:
                return {"error": "No dataset path provided."}

            out_dir = os.path.join(workdir, "gene_panels", "scgenefit")
            os.makedirs(out_dir, exist_ok=True)

            logger.info(f"scGeneFit: loading {path}")
            adata = sc.read_h5ad(path)
            if getattr(adata, "isbacked", False):
                adata = adata.to_memory()

            if not label_key or label_key not in adata.obs.columns:
                return {"error": f"label_key '{label_key}' not found in adata.obs."}

            logger.info(
                f"scGeneFit: {adata.shape}, method={method}, "
                f"n_top_genes={n_top_genes}, max_constraints={max_constraints}"
            )

            # Dense matrix required by the LP solver
            if sps.issparse(adata.X):
                X = adata.X.toarray()
            else:
                X = np.asarray(adata.X)

            y = adata.obs[label_key].astype("category").values
            d = X.shape[1]

            # Access internal scGeneFit routines
            _sample = getattr(gf, "__sample")
            _pairwise = getattr(gf, "__select_constraints_pairwise")
            _pairwise_cent = getattr(gf, "__select_constraints_centers")
            _summarised = getattr(gf, "__select_constraints_summarized")
            _lp_markers = getattr(gf, "__lp_markers")

            t0 = time.time()
            samples, samples_labels, _ = _sample(X, y, sampling_rate)
            logger.info(f"scGeneFit: sampled {len(samples)} cells in {time.time() - t0:.1f}s")

            t0 = time.time()
            if method == "pairwise_centers":
                constraints, smallest_norm = _pairwise_cent(X, y, samples, samples_labels)
            elif method == "pairwise":
                constraints, smallest_norm = _pairwise(X, y, samples, samples_labels, n_neighbors)
            else:
                constraints, smallest_norm = _summarised(X, y, redundancy)
            logger.info(
                f"scGeneFit: {constraints.shape[0]} constraints built in {time.time() - t0:.1f}s"
            )

            # Cap constraints to keep LP tractable
            if constraints.shape[0] > max_constraints:
                rng = np.random.default_rng(42)
                idx = rng.permutation(constraints.shape[0])[:max_constraints]
                constraints = constraints[idx, :]
                logger.info(f"scGeneFit: capped to {max_constraints} constraints")

            t0 = time.time()
            sol = _lp_markers(constraints, n_top_genes, smallest_norm * epsilon_param)
            logger.info(f"scGeneFit: LP solved in {time.time() - t0:.1f}s")

            weights = np.asarray(sol["x"][:d], dtype=float)

            if return_scores:
                ranked = sorted(
                    [{"gene": g, "score": float(s)} for g, s in zip(adata.var_names, weights)],
                    key=lambda d: d["score"],
                    reverse=True,
                )
                save_path = os.path.join(out_dir, "scgenefit_scores.csv")
                pd.DataFrame(ranked).to_csv(save_path, index=False)
                return {
                    "used_dataset": path,
                    "top_n": len(ranked),
                    "saved_to": save_path,
                    "genes": ranked,
                }

            order = np.argsort(-weights)[:n_top_genes]
            top = adata.var_names[order].tolist()
            save_path = os.path.join(out_dir, f"scgenefit_top_{n_top_genes}.csv")
            pd.DataFrame({"gene": top}).to_csv(save_path, index=False)

            return {
                "used_dataset": path,
                "top_n": n_top_genes,
                "saved_to": save_path,
                "genes": top,
            }

        except Exception as e:
            import traceback
            logger.error(f"scGeneFit failed: {e}\n{traceback.format_exc()}")
            return {"error": str(e)}


__all__ = ["GenePanelToolSet"]
