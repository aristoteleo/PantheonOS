

import json
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .base import SpatialBase
from ...utils.toolset import tool
from ...utils.log import logger



class SpatialBin2CellAnalysisToolSet(SpatialBase):
    """Spatial bin2cell analysis toolset"""
    
    def __init__(self, name: str = "spatial_bin2cell_analysis", workspace_path: str = None, launch_directory: str = None, worker_params: dict = None, **kwargs):
        super().__init__(name, workspace_path, launch_directory, worker_params, **kwargs)
        
        
    @tool
    def Spatial_Bin2Cell_Analysis(self, workflow_type: str, description: str = None):
        """Run a specific workflow"""
        if workflow_type == "bin2cell":
            return self.run_workflow_bin2cell()
        elif workflow_type == "read_visium":
            return self.run_workflow_read_visium()
        elif workflow_type == "cellpose_he":
            return self.run_workflow_cellpose_he()
        elif workflow_type == "expand_labels":
            return self.run_workflow_expand_labels()
        elif workflow_type == "cellpose_gex":
            return self.run_workflow_cellpose_gex()
        elif workflow_type == "salvage_secondary_labels":
            return self.run_workflow_salvage_secondary_labels()
        else:
            logger.error(f"Invalid workflow type: {workflow_type}")
            return None
        
    def run_workflow_read_visium(self):
        logger.info("\nRunning read_visium workflow")
        """Run the read_visium workflow"""
        read_visium_response = f"""
            First, check the function parameters:
            ```python
            # MANDATORY: Check help first before any omicverse function  
            import omicverse as ov
            help(ov.space.read_visium_10x)
            ```

            Then run read_visium:
            ```python
            try:
                ov.space.read_visium_10x(path, 
                    source_image_path = source_image_path)
            except Exception as e:
                print(f"❌ Read Visium failed: {{e}}")
            ```
        """
        return read_visium_response
        
    def run_workflow_cellpose_he(self):
        logger.info("\nRunning cellpose_he workflow")
        """Run the cellpose_he workflow"""
        return cellpose_he_response
        
    def run_workflow_expand_labels(self):
        logger.info("\nRunning expand_labels workflow")
        """Run the expand_labels workflow"""
        return expand_labels_response
    
    def run_workflow_cellpose_gex(self):
        logger.info("\nRunning cellpose_gex workflow")
        """Run the cellpose_gex workflow"""
        return cellpose_gex_response
    
    def run_workflow_salvage_secondary_labels(self):
        logger.info("\nRunning salvage_secondary_labels workflow")
        """Run the salvage_secondary_labels workflow"""
        return salvage_secondary_labels_response
    
    def run_workflow_bin2cell(self):
        logger.info("\nRunning bin2cell workflow")
        """Run the bin2cell workflow"""
        return bin2cell_response

cellpose_he_response = f"""

First, check the function parameters:
```python
# MANDATORY: Check help first before any omicverse function  
import omicverse as ov
help(ov.space.visium_10x_hd_cellpose_he)
```

Then run bin2cell, all parameter determined by user,under is an example:
```python
import os
os.mkdir("stardist",exist_ok=True)
he_save_path = "stardist/he_.tiff"
try:
    ov.space.visium_10x_hd_cellpose_he(
            adata,
            mpp=0.3,
            he_save_path=he_save_path,
            prob_thresh=0,
            flow_threshold=0.4,
            gpu=True,
            buffer=150,
            backend='tifffile',
        )
except Exception as e:
    print(f"❌ Bin2Cell failed: {{e}}, please check the argument is correct using help()?")
```
"""


expand_labels_response = f"""

First, check the function parameters:
```python
# MANDATORY: Check help first before any omicverse function  
import omicverse as ov
help(ov.space.visium_10x_hd_cellpose_expand)
```

Then run visium_10x_hd_cellpose_expand,max_bin_distance is the maximum distance between a bin and its expanded label:
```python
try:
    ov.space.visium_10x_hd_cellpose_expand(
        adata,
        labels_key='labels_he', 
        expanded_labels_key="labels_he_expanded",
        max_bin_distance=4,
    )
except Exception as e:
    print(f"❌ Expand Labels failed: {{e}}, please check the argument is correct using help()?")
```
"""


cellpose_gex_response = f"""

First, check the function parameters:
```python
# MANDATORY: Check help first before any omicverse function  
import omicverse as ov
help(ov.space.visium_10x_hd_cellpose_gex)
```

Then run visium_10x_hd_cellpose_gex,all parameter determined by user,under is an example: 
```python
import os
os.mkdir("stardist",exist_ok=True)
gex_save_path = "stardist/gex_.tiff"
try:
    ov.space.visium_10x_hd_cellpose_gex(
        adata,
        obs_key="n_counts_adjusted",
        log1p=False,
        mpp=0.3,
        sigma=5,
        gex_save_path=gex_save_path,
        prob_thresh=0.01,
        nms_thresh=0.1,
            gpu=True,
            buffer=150,
        )
except Exception as e:
    print(f"❌ Cellpose GEX failed: {{e}}, please check the argument is correct using help()?")
```
"""




bin2cell_response = f"""

First, check the function parameters:
```python
# MANDATORY: Check help first before any omicverse function  
import omicverse as ov
help(ov.space.bin2cell)
```

Then run bin2cell: 
```python
try:
    cdata = ov.space.bin2cell(
    adata, labels_key="labels_joint", 
    spatial_keys=["spatial", "spatial_cropped_150_buffer"])
    print("cdata",cdata)

except Exception as e:
    print(f"❌ Bin2Cell failed: {{e}},please check the labels_key is in adata.obs,spatial_keys is in adata.obsm")
```
"""

salvage_secondary_labels_response = f"""

First, check the function parameters:
```python
# MANDATORY: Check help first before any omicverse function  
import omicverse as ov
help(ov.space.salvage_secondary_labels)
```

Then run salvage_secondary_labels:
primary_label is the primary label,secondary_label is the secondary label,labels_key is the key stored in adata.obs:
primary_label is the HE label calculated by cellpose_he,secondary_label is the GEX label calculated by cellpose_gex,
labels_key is the key result stored in adata.obs,under is an example:
```python
try:
    ov.space.salvage_secondary_labels(
        adata, 
        primary_label="labels_he_expanded", 
        secondary_label="labels_gex", 
        labels_key="labels_joint"
    )
except Exception as e:
    print(f"❌ Salvage Secondary Labels failed: {{e}}")
```
"""