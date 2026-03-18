# python skills_scripts/organ-segmentation/test_skill.py \
#   --input /home/mwei26/codebase/PantheonOS/datasets/data_exp_ct-mr/CT_Case_00002_0000.nii.gz \
#   --gpu 0

# python skills_scripts/organ-segmentation/run_segmentation.py \
#   --input /home/mwei26/codebase/PantheonOS/datasets/data_exp_ct-mr/MRI_7014_0000/MRI_amos_7014_0000.nii.gz \
#   --output tmp/seg_out_mr \
#   --task total_mr \
#   --fast \
#   --statistics \
#   --roi_subset pancreas stomach liver spleen

# python skills_scripts/organ-segmentation/eval_segmentation.py \
#     --pred_dir /home/mwei26/codebase/PantheonOS/tmp/seg_out_mr \
#     --gt_dir   /home/mwei26/codebase/PantheonOS/datasets/data_exp_ct-mr/MRI_7014_0000/gt \
#     --output   datasets/data_exp_ct-mr/MRI_7014_0000/eval.json

# python skills_scripts/xray-classification/run_xray_classification.py \
#     --input /home/mwei26/codebase/PantheonOS/datasets/data_exp_xray/16747_3_1.jpg \
#     --output tmp/xray_cls_out \

python skills_scripts/xray-detection/run_xray_detection.py \
    --input /home/mwei26/codebase/PantheonOS/datasets/data_exp_xray/1.png \
    --output tmp/xray_det_out