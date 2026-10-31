# Changelog

## [0.2.0](https://github.com/pdreker/fritz_logextractor/compare/fritzlogextractor-v0.1.0...fritzlogextractor-v0.2.0) (2026-10-31)


### Features

* emit each occurrence once, across tool restarts and box reboots ([#33](https://github.com/pdreker/fritz_logextractor/issues/33)) ([197dcf7](https://github.com/pdreker/fritz_logextractor/commit/197dcf75c73f4d6386ccbe3bbec08d664486cf2c))
* log failed polls in the capture ([#28](https://github.com/pdreker/fritz_logextractor/issues/28)) ([471b52e](https://github.com/pdreker/fritz_logextractor/commit/471b52ef165a3dc96a8102a5e4b4bab4c9c1bbd5))
* refuse unknown config keys and explain the config file ([#32](https://github.com/pdreker/fritz_logextractor/issues/32)) ([df0c5f0](https://github.com/pdreker/fritz_logextractor/commit/df0c5f0dc78e5d806e14db74fb04f6a2f2b991db))
* store raw event log snapshots with --capture ([#26](https://github.com/pdreker/fritz_logextractor/issues/26)) ([51ad1df](https://github.com/pdreker/fritz_logextractor/commit/51ad1dfbed94e29c3045a5f26ef74b71d054dd15))


### Bug Fixes

* do not crash on the time info of a box that just rebooted ([#29](https://github.com/pdreker/fritz_logextractor/issues/29)) ([f4e905f](https://github.com/pdreker/fritz_logextractor/commit/f4e905f9089d2230991ae41a8eb3bacd86610f71))
* keep polling when one device fails unexpectedly ([#30](https://github.com/pdreker/fritz_logextractor/issues/30)) ([39f2707](https://github.com/pdreker/fritz_logextractor/commit/39f2707ef678289390ad8556a7e9ffa63e139286))


### Documentation

* describe the container image on Docker Hub ([#23](https://github.com/pdreker/fritz_logextractor/issues/23)) ([9c54b84](https://github.com/pdreker/fritz_logextractor/commit/9c54b847e43caa42dd55890eff44105c5a96bc6f))
* note the first test against a real box ([#25](https://github.com/pdreker/fritz_logextractor/issues/25)) ([bc080a2](https://github.com/pdreker/fritz_logextractor/commit/bc080a274a70333f200acb8874b49906c85d3fcf))
* publish versioned documentation on GitHub Pages ([#34](https://github.com/pdreker/fritz_logextractor/issues/34)) ([b276e67](https://github.com/pdreker/fritz_logextractor/commit/b276e679d3171d8334358e928cb5fddc62cbf20a))
* record what captures of a 7690 showed ([#31](https://github.com/pdreker/fritz_logextractor/issues/31)) ([fb0a71d](https://github.com/pdreker/fritz_logextractor/commit/fb0a71d280ece5be435cdc527dfc7ddf1366a174))

## 0.1.0 (2026-10-15)


### Features

* proof of concept that prints the event log as JSON lines ([469e6ed](https://github.com/pdreker/fritz_logextractor/commit/469e6edeac71e651ff7e043a2865746608297de7))


### Bug Fixes

* report the installed version instead of "develop" ([df1b9ef](https://github.com/pdreker/fritz_logextractor/commit/df1b9ef9fb05e6cb73734bd1b7c178d396607a36))


### Documentation

* name both copyright holders in NOTICE and README ([68706e5](https://github.com/pdreker/fritz_logextractor/commit/68706e536b7cdda8b922058644711eed47bcf3ea))
* warn prominently that the tool does not work yet ([7b80353](https://github.com/pdreker/fritz_logextractor/commit/7b803538472bd527295b68f0ed8d32786c59e798))
