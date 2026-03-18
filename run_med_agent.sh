# python3 -c "
# import os, asyncio
# os.environ['LLM_API_BASE'] = 'http://localhost:11434/v1'
# os.environ['LLM_API_KEY'] = 'ollama'

# from pantheon.agent import Agent

# agent = Agent(
#     name='test',
#     instructions='You are helpful.',
#     model='qwen2.5:72b-instruct-q4_K_M',
# )

# async def test():
#     result = await agent.run('Say hello in one word.')
#     print(result.content)

# asyncio.run(test())
# "

# python pantheon/med_agent_ctmr.py \
#     --image /home/mwei26/codebase/PantheonOS/datasets/data_exp_ct-mr/CT_case00002/CT_Case_00002_0000.nii.gz \
#     --gt_dir /home/mwei26/codebase/PantheonOS/datasets/data_exp_ct-mr/CT_case00002/gt \
#     --output_dir /home/mwei26/codebase/PantheonOS/tmp/seg_out_agent_ct \
#     --fast

python pantheon/med_agent_cmx.py \
    --image /home/mwei26/codebase/PantheonOS/datasets/data_exp_xray/16747_3_1.jpg \
    --output_dir /home/mwei26/codebase/PantheonOS/tmp/seg_out_agent_xray_cls