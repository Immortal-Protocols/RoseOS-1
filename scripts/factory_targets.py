"""Device-scoped factory-installer signing targets (FACTORY-TARGETS): the one place that names, per device, the committed
archive-signing binding, its bootstrap receipt and the committed source of the delivered installer's pin. Pure data and
lookups: no host, lock, secret, key, network or login.

Archive trust (the detached ssh-keygen signature over the install ZIP, verified against os/flavors/<device>.json
factorySigning) is separate from the OS signing of the target-files the installer packages. A target here never
implies that its target-files are signed, that an archive identity exists, or that an installer is factory-ready.

rango    the retained Rango installer (unchanged paths; scripts/rango-factory-inspect-wiring.py pins it).
frankel  source contract only. Its binding is committed with every value null, so factory-archive-sign.py check
         refuses and sign stops before any host or secret. No archive-signing identity, bootstrap profile, secret
         container, grant or key exists for it; those need their own owner/security approval. No installer pin source
         exists yet (Release owns packaging and inspection), so factory-delivery.py refuses (installer_unpinned).
"""

TARGETS = {
    'rango': {
        'binding': 'os/retained-rango/factory-signing.json',
        'receipt': 'os/retained-rango/factory-signing-bootstrap-receipt.json',
        'build': '2026103001',
        'installerPins': 'scripts/rango-factory-inspect-wiring.py',
        'deliverySchema': 'roseos.rango-factory-delivery.v1',
    },
    'frankel': {
        'binding': 'os/factory-signing/frankel/factory-signing.json',
        'receipt': 'os/factory-signing/frankel/factory-signing-bootstrap-receipt.json',
        'build': None,
        'installerPins': None,
        'deliverySchema': 'roseos.factory-delivery.v1',
    },
}
DEFAULT = 'rango'


class UnknownTarget(Exception):
    pass


def target(device):
    """The committed target for a plain device codename; UnknownTarget for any other device."""
    if not isinstance(device, str) or device not in TARGETS:
        raise UnknownTarget(f'no committed factory-signing target for {device!r}')
    return dict(TARGETS[device], device=device)
