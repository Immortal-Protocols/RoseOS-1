"""Rose cloud compatibility slice: PR288's Rose system on a Linux VM with A100s (MODELS-ROSE-CLOUD-001).

Same client, same wire contract, same selected models. Only the Mac-only model runtime (MLX) is replaced, one
provider at a time, behind the interfaces PR288's conductor and task manager already use. See README.md for what
is real, what is a fixture and what is not supported yet.
"""
