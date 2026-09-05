# Changelog

## [0.0.10](https://github.com/bakerkj/ha-recorder-downsampler/compare/v0.0.9...v0.0.10) (2026-09-05)


### Miscellaneous Chores

* **deps:** update anthropics/claude-code-action action to v1.0.193 ([#84](https://github.com/bakerkj/ha-recorder-downsampler/issues/84)) ([6896ed2](https://github.com/bakerkj/ha-recorder-downsampler/commit/6896ed210f87ead825373f37790e7799cbd576f1))
* **deps:** update anthropics/claude-code-action action to v1.0.194 ([#89](https://github.com/bakerkj/ha-recorder-downsampler/issues/89)) ([8d777b0](https://github.com/bakerkj/ha-recorder-downsampler/commit/8d777b02ebe9c4ae361faa9dac259e185ad0f9f3))
* **deps:** update anthropics/claude-code-action action to v1.0.205 ([#91](https://github.com/bakerkj/ha-recorder-downsampler/issues/91)) ([70df931](https://github.com/bakerkj/ha-recorder-downsampler/commit/70df931d219dfbf896143bef632b28ed9cb85ade))
* **deps:** update anthropics/claude-code-action action to v1.0.211 ([#97](https://github.com/bakerkj/ha-recorder-downsampler/issues/97)) ([d0a0227](https://github.com/bakerkj/ha-recorder-downsampler/commit/d0a0227551af70f6a0673448a54b2c520bbc623c))
* **deps:** update anthropics/claude-code-action action to v1.0.212 ([#98](https://github.com/bakerkj/ha-recorder-downsampler/issues/98)) ([23c466e](https://github.com/bakerkj/ha-recorder-downsampler/commit/23c466e29a4232f8807041168ba7b0217b89ffa7))
* **deps:** update astral-sh/setup-uv action to v10 ([#87](https://github.com/bakerkj/ha-recorder-downsampler/issues/87)) ([22b58db](https://github.com/bakerkj/ha-recorder-downsampler/commit/22b58dbf8f147f62e46c83a293f6cc139c55bf68))
* **deps:** update dependency uv to v0.12.10 ([#99](https://github.com/bakerkj/ha-recorder-downsampler/issues/99)) ([6a68b45](https://github.com/bakerkj/ha-recorder-downsampler/commit/6a68b45e6e4893aaa048be51a8ff9b2de323de81))
* **deps:** update dependency uv to v0.12.5 ([#85](https://github.com/bakerkj/ha-recorder-downsampler/issues/85)) ([ebf4786](https://github.com/bakerkj/ha-recorder-downsampler/commit/ebf4786eb6b0b5af691f3d757822c425e1ac7260))
* **deps:** update dependency uv to v0.12.7 ([#92](https://github.com/bakerkj/ha-recorder-downsampler/issues/92)) ([17a9541](https://github.com/bakerkj/ha-recorder-downsampler/commit/17a95419bce43858f3b86bddb61801f01c73b422))
* **deps:** update dependency uv to v0.12.8 ([#94](https://github.com/bakerkj/ha-recorder-downsampler/issues/94)) ([3ac99dc](https://github.com/bakerkj/ha-recorder-downsampler/commit/3ac99dc47e1e5f6459d7102508e7b2ddd5d40b53))
* **deps:** update dependency uv to v0.12.9 ([#95](https://github.com/bakerkj/ha-recorder-downsampler/issues/95)) ([380fe91](https://github.com/bakerkj/ha-recorder-downsampler/commit/380fe915e725ca0db3d0f29492a60f1811b333e9))
* **deps:** update pre-commit hook astral-sh/ruff-pre-commit to v0.16.3 ([#86](https://github.com/bakerkj/ha-recorder-downsampler/issues/86)) ([8bdc640](https://github.com/bakerkj/ha-recorder-downsampler/commit/8bdc6405f97ade2f8770441ae471bc0a8cf92a08))
* **deps:** update pre-commit hook astral-sh/ruff-pre-commit to v0.16.4 ([#90](https://github.com/bakerkj/ha-recorder-downsampler/issues/90)) ([5645715](https://github.com/bakerkj/ha-recorder-downsampler/commit/564571579dba866b596d050a34fc1f1c3db7f26c))
* **deps:** update pre-commit hook astral-sh/ruff-pre-commit to v0.16.5 ([#93](https://github.com/bakerkj/ha-recorder-downsampler/issues/93)) ([edaeab5](https://github.com/bakerkj/ha-recorder-downsampler/commit/edaeab542daba220df4bea1072f05633d8d7f452))
* **deps:** update pre-commit hook astral-sh/ruff-pre-commit to v0.16.6 ([#96](https://github.com/bakerkj/ha-recorder-downsampler/issues/96)) ([608e01c](https://github.com/bakerkj/ha-recorder-downsampler/commit/608e01cf06f080a0f79bb3d71291bfb8a7183c4f))

## [0.0.9](https://github.com/bakerkj/ha-recorder-downsampler/compare/v0.0.8...v0.0.9) (2026-08-10)


### Bug Fixes

* name mirrors of sources that have no name of their own ([#82](https://github.com/bakerkj/ha-recorder-downsampler/issues/82)) ([086172e](https://github.com/bakerkj/ha-recorder-downsampler/commit/086172ed4afc0aa86d2d9dc93232d91c78ece083))

## [0.0.8](https://github.com/bakerkj/ha-recorder-downsampler/compare/v0.0.7...v0.0.8) (2026-08-10)


### Bug Fixes

* never release a device that still holds our mirrors ([#80](https://github.com/bakerkj/ha-recorder-downsampler/issues/80)) ([d864291](https://github.com/bakerkj/ha-recorder-downsampler/commit/d86429190f9f841dd29b43d98c7b37995d4154a4))

## [0.0.7](https://github.com/bakerkj/ha-recorder-downsampler/compare/v0.0.6...v0.0.7) (2026-08-09)


### Bug Fixes

* **pre-commit:** set default_stages so hooks skip commit-msg by default ([#73](https://github.com/bakerkj/ha-recorder-downsampler/issues/73)) ([4901ac8](https://github.com/bakerkj/ha-recorder-downsampler/commit/4901ac85adc7b9f93177b0df26fec2592edaed0e))
* release device ownership instead of co-owning source devices ([#78](https://github.com/bakerkj/ha-recorder-downsampler/issues/78)) ([9aae6aa](https://github.com/bakerkj/ha-recorder-downsampler/commit/9aae6aa83d5a4f0108626c52dfded9ad373d98d6))
* scope dev-tooling auto-merge by depType ([#71](https://github.com/bakerkj/ha-recorder-downsampler/issues/71)) ([fcf90bc](https://github.com/bakerkj/ha-recorder-downsampler/commit/fcf90bcadf1f469cec3adbc224943a31773119aa))
* use toml updater type for uv.lock in release-please ([#64](https://github.com/bakerkj/ha-recorder-downsampler/issues/64)) ([df423a6](https://github.com/bakerkj/ha-recorder-downsampler/commit/df423a6f2a0b13890b5c9504b5f3b41a7bf8454d))


### Miscellaneous Chores

* **deps:** pin uv to 0.12.2 ([#72](https://github.com/bakerkj/ha-recorder-downsampler/issues/72)) ([6ac93fd](https://github.com/bakerkj/ha-recorder-downsampler/commit/6ac93fd5f56352b78172b46d86cd78637e1938cb))
* **deps:** update anthropics/claude-code-action action to v1.0.184 ([#74](https://github.com/bakerkj/ha-recorder-downsampler/issues/74)) ([2ad3aae](https://github.com/bakerkj/ha-recorder-downsampler/commit/2ad3aae730331cead9343e50e811a2e111e5b59e))
* **deps:** update dependency uv to ==0.12.* ([#66](https://github.com/bakerkj/ha-recorder-downsampler/issues/66)) ([f974f1a](https://github.com/bakerkj/ha-recorder-downsampler/commit/f974f1ae949ee7cfcbfd259055f9ae513552fc49))
* **deps:** update dependency uv to v0.12.3 ([#76](https://github.com/bakerkj/ha-recorder-downsampler/issues/76)) ([bbdba0f](https://github.com/bakerkj/ha-recorder-downsampler/commit/bbdba0f987da97b3e9cfcdf1c7ad582e25f7558c))
* **deps:** update github-actions ([#75](https://github.com/bakerkj/ha-recorder-downsampler/issues/75)) ([355f55f](https://github.com/bakerkj/ha-recorder-downsampler/commit/355f55fc33e4d462404834e0f2703160f4b8a30e))
* **deps:** update home-assistant/actions digest to ab22029 ([#69](https://github.com/bakerkj/ha-recorder-downsampler/issues/69)) ([5f8f1f1](https://github.com/bakerkj/ha-recorder-downsampler/commit/5f8f1f11876c531114fe2ee67653601690712023))
* **deps:** update j178/prek-action action to v3 ([#68](https://github.com/bakerkj/ha-recorder-downsampler/issues/68)) ([b60243e](https://github.com/bakerkj/ha-recorder-downsampler/commit/b60243e77b9ad9f2d6779d89b77a2def06d0bb2e))
* **deps:** update pre-commit hook astral-sh/ruff-pre-commit to v0.16.1 ([#67](https://github.com/bakerkj/ha-recorder-downsampler/issues/67)) ([dc84655](https://github.com/bakerkj/ha-recorder-downsampler/commit/dc846557e9f8ad22e36c143ce10beb8ee9ff4272))
* **deps:** update pre-commit hook astral-sh/ruff-pre-commit to v0.16.2 ([#77](https://github.com/bakerkj/ha-recorder-downsampler/issues/77)) ([d25050f](https://github.com/bakerkj/ha-recorder-downsampler/commit/d25050f7cef58f5856be69b099cd85c8ff34dcd5))
* **deps:** update pre-commit hook python-jsonschema/check-jsonschema to v0.38.0 ([#79](https://github.com/bakerkj/ha-recorder-downsampler/issues/79)) ([f815f5e](https://github.com/bakerkj/ha-recorder-downsampler/commit/f815f5e1951463e88d00af6bee39c6e268e3cc72))
* keep uv.lock project version synced with release-please ([#63](https://github.com/bakerkj/ha-recorder-downsampler/issues/63)) ([8c4e93b](https://github.com/bakerkj/ha-recorder-downsampler/commit/8c4e93b3e0fdb3da925377187f1206563e4baa14))


### Continuous Integration

* enable renovate auto-merge for CI-only updates ([#70](https://github.com/bakerkj/ha-recorder-downsampler/issues/70)) ([d9489d0](https://github.com/bakerkj/ha-recorder-downsampler/commit/d9489d02925307be75c462b50b9e87e3fa47952a))

## [0.0.6](https://github.com/bakerkj/ha-recorder-downsampler/compare/v0.0.5...v0.0.6) (2026-07-26)


### Miscellaneous Chores

* **deps:** update anthropics/claude-code-action action to v1.0.183 ([#59](https://github.com/bakerkj/ha-recorder-downsampler/issues/59)) ([5e699bc](https://github.com/bakerkj/ha-recorder-downsampler/commit/5e699bcb5d12fd2ace06e22a2267c9e7f28eb3e9))
* update uv.lock ([#62](https://github.com/bakerkj/ha-recorder-downsampler/issues/62)) ([a30d842](https://github.com/bakerkj/ha-recorder-downsampler/commit/a30d842be386facf70bc4e1ea500140d385b9aba))


### Continuous Integration

* add hassfest manifest validation ([#61](https://github.com/bakerkj/ha-recorder-downsampler/issues/61)) ([fc12130](https://github.com/bakerkj/ha-recorder-downsampler/commit/fc12130c9eb9c09bb0ae68aa464b8c348eb6b2dd))

## [0.0.5](https://github.com/bakerkj/ha-recorder-downsampler/compare/v0.0.4...v0.0.5) (2026-07-24)


### Miscellaneous Chores

* **deps:** update actions/setup-python action to v7 ([#51](https://github.com/bakerkj/ha-recorder-downsampler/issues/51)) ([87105c6](https://github.com/bakerkj/ha-recorder-downsampler/commit/87105c641164622743aeee86ec1b5ad30d239787))
* **deps:** update anthropics/claude-code-action action to v1.0.165 ([#42](https://github.com/bakerkj/ha-recorder-downsampler/issues/42)) ([0989c69](https://github.com/bakerkj/ha-recorder-downsampler/commit/0989c69551f0b6a70ab3dd22b26148db7d2c07ba))
* **deps:** update anthropics/claude-code-action action to v1.0.170 ([#47](https://github.com/bakerkj/ha-recorder-downsampler/issues/47)) ([f1dbcc5](https://github.com/bakerkj/ha-recorder-downsampler/commit/f1dbcc55c086c52e426c1f1ef8089f2b7ba670ca))
* **deps:** update anthropics/claude-code-action action to v1.0.181 ([#58](https://github.com/bakerkj/ha-recorder-downsampler/issues/58)) ([0ccff60](https://github.com/bakerkj/ha-recorder-downsampler/commit/0ccff603c13dc6fd2f543a7b27036e5368d62ec1))
* **deps:** update astral-sh/setup-uv action to v8.3.0 ([#43](https://github.com/bakerkj/ha-recorder-downsampler/issues/43)) ([74494cb](https://github.com/bakerkj/ha-recorder-downsampler/commit/74494cb9c1450767f24f94ac56b6f53373aaa362))
* **deps:** update astral-sh/setup-uv action to v9 ([#54](https://github.com/bakerkj/ha-recorder-downsampler/issues/54)) ([a81d199](https://github.com/bakerkj/ha-recorder-downsampler/commit/a81d1995043811bba46184f3872d5c1b794527dc))
* **deps:** update github-actions ([#40](https://github.com/bakerkj/ha-recorder-downsampler/issues/40)) ([070ad6a](https://github.com/bakerkj/ha-recorder-downsampler/commit/070ad6aad5b826fdb47f597bc851d098cac63cc6))
* **deps:** update github-actions ([#45](https://github.com/bakerkj/ha-recorder-downsampler/issues/45)) ([2302713](https://github.com/bakerkj/ha-recorder-downsampler/commit/230271356066e2b8ced2576ce0f3b0aeb4ef01e3))
* **deps:** update github-actions ([#50](https://github.com/bakerkj/ha-recorder-downsampler/issues/50)) ([73a592c](https://github.com/bakerkj/ha-recorder-downsampler/commit/73a592ca6c98cd92c93d7cb428297dca4cbd6b75))
* **deps:** update github-actions ([#52](https://github.com/bakerkj/ha-recorder-downsampler/issues/52)) ([9b9ac9c](https://github.com/bakerkj/ha-recorder-downsampler/commit/9b9ac9cabf445bfb730ace43566db8e254013906))
* **deps:** update pre-commit hook astral-sh/ruff-pre-commit to v0.16.0 ([#56](https://github.com/bakerkj/ha-recorder-downsampler/issues/56)) ([d100a5e](https://github.com/bakerkj/ha-recorder-downsampler/commit/d100a5e03eb1369e37236f3604274dac68ea152a))
* **deps:** update pre-commit hook python-jsonschema/check-jsonschema to v0.37.4 ([#38](https://github.com/bakerkj/ha-recorder-downsampler/issues/38)) ([61c4002](https://github.com/bakerkj/ha-recorder-downsampler/commit/61c400214dfd5330b2d9786621333b4879ca7eef))
* **deps:** update pre-commit hook rbubley/mirrors-prettier to v3.9.4 ([#41](https://github.com/bakerkj/ha-recorder-downsampler/issues/41)) ([f870e08](https://github.com/bakerkj/ha-recorder-downsampler/commit/f870e088ed82a60ce58cf216457dbe57faddf7d0))
* **deps:** update pre-commit hook rbubley/mirrors-prettier to v3.9.6 ([#55](https://github.com/bakerkj/ha-recorder-downsampler/issues/55)) ([0011ce9](https://github.com/bakerkj/ha-recorder-downsampler/commit/0011ce9dbc3cc240d2bdbbba908af5320811880b))
* **deps:** update pre-commit hooks ([#48](https://github.com/bakerkj/ha-recorder-downsampler/issues/48)) ([683c874](https://github.com/bakerkj/ha-recorder-downsampler/commit/683c87438f63115325bd0f134ee681dae9835c28))
* **deps:** update pre-commit hooks ([#49](https://github.com/bakerkj/ha-recorder-downsampler/issues/49)) ([9170ba7](https://github.com/bakerkj/ha-recorder-downsampler/commit/9170ba7f7aab2da081ea46ef06b2713c9271b3c5))
* **renovate:** drop redundant alternation in npm-in-pre-commit regex ([#53](https://github.com/bakerkj/ha-recorder-downsampler/issues/53)) ([da810fe](https://github.com/bakerkj/ha-recorder-downsampler/commit/da810fe3dbadba0704fbadfb9eda87764ae6df44))


### Documentation

* add MIT license ([#44](https://github.com/bakerkj/ha-recorder-downsampler/issues/44)) ([dde0a5f](https://github.com/bakerkj/ha-recorder-downsampler/commit/dde0a5fb7c5c3a5ffdc4cd49aa0ddf8da3ae2f55))


### Tests

* guard disable_http_server fixture for HA dev ([#46](https://github.com/bakerkj/ha-recorder-downsampler/issues/46)) ([6c5305e](https://github.com/bakerkj/ha-recorder-downsampler/commit/6c5305e0cf7b50dce244d4f94f0bf31b33ce85b5))

## [0.0.4](https://github.com/bakerkj/ha-recorder-downsampler/compare/v0.0.3...v0.0.4) (2026-06-28)


### Bug Fixes

* fall back to registry unit when source attrs omit it ([#36](https://github.com/bakerkj/ha-recorder-downsampler/issues/36)) ([f17676d](https://github.com/bakerkj/ha-recorder-downsampler/commit/f17676df4bc7b66806f969c54140855b0c87a440))


### Miscellaneous Chores

* **deps:** pin dependencies ([#25](https://github.com/bakerkj/ha-recorder-downsampler/issues/25)) ([12a1464](https://github.com/bakerkj/ha-recorder-downsampler/commit/12a1464038ad8891266c8530313d842dcb128a91))
* **deps:** update anthropics/claude-code-action action to v1.0.157 ([#32](https://github.com/bakerkj/ha-recorder-downsampler/issues/32)) ([222773b](https://github.com/bakerkj/ha-recorder-downsampler/commit/222773b8fd5e81eb721f0e64501efc1d14b4f033))
* **deps:** update commitlint monorepo to v21 ([#17](https://github.com/bakerkj/ha-recorder-downsampler/issues/17)) ([c43f9c2](https://github.com/bakerkj/ha-recorder-downsampler/commit/c43f9c2a7a417767a1bb45a353b94bf902eb5c23))
* **deps:** update github-actions ([#11](https://github.com/bakerkj/ha-recorder-downsampler/issues/11)) ([a5011ff](https://github.com/bakerkj/ha-recorder-downsampler/commit/a5011ff6b472f4f0807e98ddcf64d887aee2b60f))
* **deps:** update github-actions ([#28](https://github.com/bakerkj/ha-recorder-downsampler/issues/28)) ([cd3db43](https://github.com/bakerkj/ha-recorder-downsampler/commit/cd3db4380eb1be69725f47914d781a07963e9c7b))
* **deps:** update github-actions ([#29](https://github.com/bakerkj/ha-recorder-downsampler/issues/29)) ([7bd70fe](https://github.com/bakerkj/ha-recorder-downsampler/commit/7bd70fefed7773f5cccfc45d963af7a17cec4b17))
* **deps:** update github-actions ([#31](https://github.com/bakerkj/ha-recorder-downsampler/issues/31)) ([96f51c9](https://github.com/bakerkj/ha-recorder-downsampler/commit/96f51c9a55d7f2daf2fc00a4271b9d7dd9b4745a))
* **deps:** update github-actions ([#34](https://github.com/bakerkj/ha-recorder-downsampler/issues/34)) ([22d6768](https://github.com/bakerkj/ha-recorder-downsampler/commit/22d67685fac33008446368f4cb4e12f97cf489fe))
* **deps:** update github-actions to v1.0.153 ([#27](https://github.com/bakerkj/ha-recorder-downsampler/issues/27)) ([9fdbe50](https://github.com/bakerkj/ha-recorder-downsampler/commit/9fdbe50ea2dfb8fc421398401760ecd70de1a8da))
* **deps:** update github-actions to v7 ([#22](https://github.com/bakerkj/ha-recorder-downsampler/issues/22)) ([a9702e7](https://github.com/bakerkj/ha-recorder-downsampler/commit/a9702e7da0d1ff839845c6cf4087cd3a3c0a76dc))
* **deps:** update pre-commit hook alessandrojcm/commitlint-pre-commit-hook to v9.26.0 ([#33](https://github.com/bakerkj/ha-recorder-downsampler/issues/33)) ([6298b12](https://github.com/bakerkj/ha-recorder-downsampler/commit/6298b12ff06f3d6bd7a88422f4b615f8f18c055e))
* **deps:** update pre-commit hook astral-sh/ruff-pre-commit to v0.15.20 ([#35](https://github.com/bakerkj/ha-recorder-downsampler/issues/35)) ([5911e06](https://github.com/bakerkj/ha-recorder-downsampler/commit/5911e061a69f33e82f6a2079af89951fbbd27070))
* **deps:** update pre-commit hook rbubley/mirrors-prettier to v3.9.1 ([#37](https://github.com/bakerkj/ha-recorder-downsampler/issues/37)) ([b6efce6](https://github.com/bakerkj/ha-recorder-downsampler/commit/b6efce699f7625260faf54bd5f542af7e9cc3115))
* **deps:** update pre-commit hooks ([#20](https://github.com/bakerkj/ha-recorder-downsampler/issues/20)) ([07f4098](https://github.com/bakerkj/ha-recorder-downsampler/commit/07f4098d16b1e3baeb4df00120d6920e79cff54d))
* **deps:** update pre-commit hooks ([#30](https://github.com/bakerkj/ha-recorder-downsampler/issues/30)) ([e5835b9](https://github.com/bakerkj/ha-recorder-downsampler/commit/e5835b9e955416dec0dca5e5004237b37f75102b))
* **deps:** update pre-commit hooks to v0.15.16 ([#12](https://github.com/bakerkj/ha-recorder-downsampler/issues/12)) ([efec537](https://github.com/bakerkj/ha-recorder-downsampler/commit/efec5377fe69b5673a62bbf458e52e854054ded2))
* **deps:** update pre-commit hooks to v0.15.18 ([#21](https://github.com/bakerkj/ha-recorder-downsampler/issues/21)) ([35246c6](https://github.com/bakerkj/ha-recorder-downsampler/commit/35246c6fe5599f242be272f062280003bb0defe4))
* **deps:** update pre-commit hooks to v3.8.4 ([#19](https://github.com/bakerkj/ha-recorder-downsampler/issues/19)) ([1b3788c](https://github.com/bakerkj/ha-recorder-downsampler/commit/1b3788c3815e986514e4a11a148fc722f32ec4e9))
* normalize workflow file extensions to .yaml ([#26](https://github.com/bakerkj/ha-recorder-downsampler/issues/26)) ([03b9d7c](https://github.com/bakerkj/ha-recorder-downsampler/commit/03b9d7ccac8b6af93e850940f552614846242f82))
* pin GitHub Actions to triple-digit tags ([#23](https://github.com/bakerkj/ha-recorder-downsampler/issues/23)) ([6de12e2](https://github.com/bakerkj/ha-recorder-downsampler/commit/6de12e20fbdf47872537972aa92f0fccb53b5f65))
* pin uv to ==0.11.* and let Renovate track it ([#18](https://github.com/bakerkj/ha-recorder-downsampler/issues/18)) ([4246db0](https://github.com/bakerkj/ha-recorder-downsampler/commit/4246db0c5e700c18d5230faaa270b7a926fd273c))
* **renovate:** pin GitHub Action digests to semver ([#24](https://github.com/bakerkj/ha-recorder-downsampler/issues/24)) ([5690451](https://github.com/bakerkj/ha-recorder-downsampler/commit/5690451a94c5a4240bb9ac3366d87b89f6a42349))
* **renovate:** track npm deps pinned via pre-commit additional_dependencies ([#15](https://github.com/bakerkj/ha-recorder-downsampler/issues/15)) ([ce2fe0f](https://github.com/bakerkj/ha-recorder-downsampler/commit/ce2fe0f426567f20d026fc1dac05980a66640487))


### Continuous Integration

* **claude:** add Claude Code GitHub workflows ([#13](https://github.com/bakerkj/ha-recorder-downsampler/issues/13)) ([c2b3490](https://github.com/bakerkj/ha-recorder-downsampler/commit/c2b3490338288905c596d1f7cc61bbd1a012e078))
* drop python-version-file from setup-uv step ([#16](https://github.com/bakerkj/ha-recorder-downsampler/issues/16)) ([4b125e1](https://github.com/bakerkj/ha-recorder-downsampler/commit/4b125e1350296b24da9a081963c8925601f0fdd9))
* **ha-dev-compat:** enable uv cache for pinned-dep install ([#8](https://github.com/bakerkj/ha-recorder-downsampler/issues/8)) ([5dbd4f3](https://github.com/bakerkj/ha-recorder-downsampler/commit/5dbd4f35c8f11cbdf6d6616a14fb15ecf2a81617))
* **prek:** cache prek hook envs across runs ([#10](https://github.com/bakerkj/ha-recorder-downsampler/issues/10)) ([ee3b918](https://github.com/bakerkj/ha-recorder-downsampler/commit/ee3b918fde992f80a94e5dd541005f41982c3691))
* use python-version-file in setup-python and setup-uv ([#14](https://github.com/bakerkj/ha-recorder-downsampler/issues/14)) ([b1dc982](https://github.com/bakerkj/ha-recorder-downsampler/commit/b1dc982c8fe7b7c4aed7e616e01c263fe8e159c2))

## [0.0.3](https://github.com/bakerkj/ha-recorder-downsampler/compare/v0.0.2...v0.0.3) (2026-06-01)


### Features

* **aggregation:** add circular_mean for angular sources ([#6](https://github.com/bakerkj/ha-recorder-downsampler/issues/6)) ([9efdf6a](https://github.com/bakerkj/ha-recorder-downsampler/commit/9efdf6a5aaf8f43bfdd526fb1bfe0c5b40d84f79))
* **aggregation:** time-weight mean and circular_mean by sample dwell ([#7](https://github.com/bakerkj/ha-recorder-downsampler/issues/7)) ([8e308a9](https://github.com/bakerkj/ha-recorder-downsampler/commit/8e308a9873dc04077c2a45cac99788006cd52248))


### Miscellaneous Chores

* **deps:** update pre-commit hook astral-sh/ruff-pre-commit to v0.15.15 ([#4](https://github.com/bakerkj/ha-recorder-downsampler/issues/4)) ([071a4bd](https://github.com/bakerkj/ha-recorder-downsampler/commit/071a4bdaacb2bb0c2b0986fbd1bde11836bba720))

## [0.0.2](https://github.com/bakerkj/ha-recorder-downsampler/compare/v0.0.1...v0.0.2) (2026-05-26)


### Features

* initial public release of recorder_downsampler ([7e93711](https://github.com/bakerkj/ha-recorder-downsampler/commit/7e9371181c10dc590f311aaa2c648431f8558ed4))


### Miscellaneous Chores

* **deps:** update pre-commit hook astral-sh/ruff-pre-commit to v0.15.14 ([#3](https://github.com/bakerkj/ha-recorder-downsampler/issues/3)) ([5637cd9](https://github.com/bakerkj/ha-recorder-downsampler/commit/5637cd9655ea522f08d027071c69fff75cf78b30))

## Changelog
