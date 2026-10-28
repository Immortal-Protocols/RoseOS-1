OS's Cuttlefish alpha delta as it was at commit `f21bf68322b3fa0a8924840554ab3e9398f79125` (PR61), the delta of the
historical preparation `b02dafbb…` (receipt `18582916…`). Unmodified bytes, read with `git show` of that commit, so the
successor tests can prepare a real predecessor without git history (CI checks out one commit). They are test fixtures,
not current build inputs; the current delta is `os/patches/cuttlefish-alpha-no-updater.patch` at `156ccb8e`.

| File | Path at f21bf683 | sha256 |
| --- | --- | --- |
| `cuttlefish-alpha-no-updater.patch` | `os/patches/cuttlefish-alpha-no-updater.patch` | `e000da0d1247dfcb341bc97df3098a2ed57faed31af0e5a837733537ae567858` |
| `cuttlefish-product-contract.json` | `os/alpha/cuttlefish-product-contract.json` | `c825aa19d46e08fa59816d3b0b4b3588358b9df99e09e9b7ba476e1da70a7e9d` |
