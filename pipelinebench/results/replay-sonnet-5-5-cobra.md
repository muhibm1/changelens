# Replay eval: `anthropic`, 8 merged changes

| metric | value |
| --- | --- |
| changes | 8 |
| errors | 0 |
| changes warned | 1 |
| warns | 3 |
| warns per change | 0.38 |
| confirmed | 0 |
| plausible | 3 |
| noise | 0 |
| unjudged | 0 |
| setup | 0 |
| confirmed recall | 0/0 |
| tokens in | 96893 |
| tokens out | 2301 |

| repo | commit | change | severity | warnings |
| --- | --- | --- | --- | --- |
| cobra | `24ada7f` | Remove the default completion cmd if it is alone (#1559) | inform | none |
| cobra | `5bef9d8` | Fix --version help and output for plugins (#2180) | inform | none |
| cobra | `6c3c116` | feat: add CompletionFunc type (#2220) | inform | none |
| cobra | `8607918` | feat: make InitDefaultCompletionCmd public (#1467) | inform | none |
| cobra | `a0a6ae0` | Improve API to get flag completion function (#2063) | inform | none |
| cobra | `a4ab3fa` | powershell completion with custom comp (#1208) | warn | **plausible** `powershell_completions.go:71` Command line split on single space loses quoting and breaks on multiple spaces; **plausible** `powershell_completions.go:99` Directive parsing crashes when the program outputs nothing; **plausible** `powershell_completions.go:164` Where-Object filter block rewrites names and emits the wrong value for equal-sign flags |
| cobra | `b84ef40` | Rename BashCompDirectives to ShellCompDirectives (#1082) | inform | none |
| cobra | `fce8d8a` | Expose ValidateRequiredFlags and ValidateFlagGroups (#1760) | inform | none |
