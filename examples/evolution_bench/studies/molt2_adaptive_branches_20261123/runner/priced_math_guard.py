"""Study-only provider/price/model/seed constraints above the tested shared ledger."""
import os
from model_io_queue import AdmissionDenied
from receipt_guard import ReceiptGuard

ACTIVE_GUARD = None


class PricedMathGuard(ReceiptGuard):
    def __init__(self, *, model_seed=0, **kwargs):
        super().__init__(**kwargs)
        self.model_seed = int(model_seed)
        self.logical_calls = 0

    def validate(self, kwargs):
        super().validate(kwargs)
        if kwargs.get('model') != 'deepseek/deepseek-v4.1-flash':
            self.stop('Unexpected model refused before API admission')
            raise AdmissionDenied('Unexpected model')
        kwargs['extra_body']['provider']['max_price'] = {'prompt': .151, 'completion': .451}
        kwargs['seed'] = self.model_seed * 1000 + self.logical_calls
        self.logical_calls += 1


def install(*, environ=None, sdk_classes=None):
    env = os.environ if environ is None else environ
    guard = PricedMathGuard(
        caller_lab=env['MOLT2_LAB'], ledger_root=env['MOLT2_LEDGER_ROOT'],
        io_dir=env['MOLT_LLM_IO_DIR'], provider=env['MOLT2_PROVIDER'],
        limit=float(env['MOLT2_GLOBAL_USD']), run_id=env['MOLT2_RUN_ID'],
        model_seed=int(env['MOLT2_MODEL_SEED']),
        secrets=[env[k] for k in ['OPENROUTER_API_KEY', 'OPENAI_API_KEY'] if env.get(k)],
        wait_timeout=300, poll_interval=.25,
    )
    if sdk_classes is None:
        from openai.resources.chat.completions import AsyncCompletions, Completions
        sdk_classes = AsyncCompletions, Completions
    guard.restore_sdk = guard.install(*sdk_classes)
    global ACTIVE_GUARD
    ACTIVE_GUARD = guard
    return guard
