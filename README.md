# vrml1tovrml2

Linux-native reimplementation of the old Windows `vrml1tovrml2.exe` workflow.

这个仓库原先只有老的 Windows 二进制和你导出的逆向资料。现在补上了一个可直接在 Linux 上运行的命令行实现：

- 可执行入口：`./vrml1tovrml2`
- Rust CLI 入口：`./src/main.rs`
- Python 兼容 API 入口：`./vrml1tovrml2.py`
- 模块化源码目录：`./vrml1tovrml2_pkg/`
- 示例输入：`examples/sample_v1.wrl`
- 示例输出：`examples/sample_v2.wrl`

当前命令行入口已经改成 Rust 实现；它负责参数透传、工作目录和退出码处理，并转调现有的 Python 转换核心。

## WRL 目录建议

为了后续持续回归，建议把用于对比的 `.wrl` 样例统一按 case 组织在 `wrl/cases/` 下：

```text
wrl/
  cases/
    <case-name>/
      input.v1.wrl
      baseline.v2.from_exe.wrl
      current.v2.from_python.wrl
```

这样每个测试样例的输入、Windows 真值输出、当前实现输出都放在同一个目录里，后续新增样例、做 diff、写自动化脚本都会更顺手。

当前已整理的 case：

- [sample_minimal](/home/cesar/vrml1tovrml2/wrl/cases/sample_minimal)
- [ansys_test_from_ansys_1](/home/cesar/vrml1tovrml2/wrl/cases/ansys_test_from_ansys_1)

其中 [wrl/cases/ansys_test_from_ansys_1/baseline.v2.from_exe.wrl](/home/cesar/vrml1tovrml2/wrl/cases/ansys_test_from_ansys_1/baseline.v2.from_exe.wrl)
已经作为项目内真值保存，后续迭代默认直接使用这个文件，不需要再调用一次原始 `.exe`。

## 用法

```bash
./vrml1tovrml2 input_v1.wrl output_v2.wrl
```

或者输出到标准输出：

```bash
./vrml1tovrml2 input_v1.wrl
```

调试日志：

```bash
./vrml1tovrml2 input_v1.wrl output_v2.wrl --verbose
```

如果环境里安装了 `tqdm`，可以打开输入读取进度条：

```bash
./vrml1tovrml2 --progress input_v1.wrl output_v2.wrl
```

如果没有安装 `tqdm`，程序会自动降级，不会报错。

## 当前已实现并验证的节点

- 分组与层级：`Separator`, `TransformSeparator`, `Group`, `Switch`, `LOD`
- 变换：`Translation`, `Rotation`, `Scale`, `Transform`, `MatrixTransform`
- 几何与索引：`Coordinate3`, `IndexedFaceSet`, `IndexedLineSet`, `PointSet`
- 基础体：`Cube`, `Cone`, `Cylinder`, `Sphere`
- 外观相关：`Material`, `MaterialBinding`, `Normal`, `NormalBinding`, `ShapeHints`
- 纹理相关：`Texture2`, `TextureCoordinate2`, `Texture2Transform`, `Texture2Transformation`
- 文本：`AsciiText`, `FontStyle`
- 其他：`DirectionalLight`, `PointLight`, `SpotLight`, `PerspectiveCamera`, `OrthographicCamera`, `WWWAnchor`, `WWWInline`
- 共享定义：`DEF` / `USE`

## 已验证示例

```bash
./vrml1tovrml2 examples/sample_v1.wrl examples/sample_v2.wrl
./vrml1tovrml2 examples/sample_defs_v1.wrl examples/sample_defs_v2.wrl
./scripts/regenerate_testset.sh
```

两个示例都能成功生成 `#VRML V2.0 utf8` 输出。

## 当前限制

- 这是一个“按逆向资料和 VRML 语义重建”的 Linux 实现，不是逐函数逐字节复刻原 DLL。
- 目前优先覆盖主流 VRML 1.0/Open Inventor 风格节点，对一些非常少见的 SGI/Cosmo 专有扩展节点还没有完整补齐。
- `MatrixTransform` 目前做的是常见仿射场景下的近似转换，重点保留平移和轴向缩放。
- 复杂绑定、少见字段组合、历史兼容细节，仍建议拿你的真实 `.wrl` 样本继续回归补全。
- 当前已经完成两阶段内存优化中的前两步：输入读取、tokenizer、输出写入都支持流式；`Material`、`Coordinate3`、`Normal`、`IndexedFaceSet`、`IndexedLineSet` 的大数组字段会优先落到临时 spool 文件而不是长期保存在 Python 大列表里。
- 但转换器整体还没有做到完全“边解析边输出”，节点级 AST 和部分中间状态仍然会驻留内存，所以这还不是最终版的大文件方案。

## 代码说明

- Rust CLI 入口在 [src/main.rs](/home/cesar/vrml1tovrml2/src/main.rs)。
- Python 兼容 API 入口在 [vrml1tovrml2.py](/home/cesar/vrml1tovrml2/vrml1tovrml2.py)。
- 模块化实现位于 [vrml1tovrml2_pkg](/home/cesar/vrml1tovrml2/vrml1tovrml2_pkg)：
  - [common.py](/home/cesar/vrml1tovrml2/vrml1tovrml2_pkg/common.py)
  - [specs.py](/home/cesar/vrml1tovrml2/vrml1tovrml2_pkg/specs.py)
  - [parser.py](/home/cesar/vrml1tovrml2/vrml1tovrml2_pkg/parser.py)
  - [converter.py](/home/cesar/vrml1tovrml2/vrml1tovrml2_pkg/converter.py)
  - [writer.py](/home/cesar/vrml1tovrml2/vrml1tovrml2_pkg/writer.py)
  - [cli.py](/home/cesar/vrml1tovrml2/vrml1tovrml2_pkg/cli.py)
  - [progress.py](/home/cesar/vrml1tovrml2/vrml1tovrml2_pkg/progress.py)
- 回归脚本在 [scripts/regenerate_testset.sh](/home/cesar/vrml1tovrml2/scripts/regenerate_testset.sh)。

## 并行化评估

- 对“单个超大 `.wrl` 文件内部”直接并行处理，目前不建议强上。
  原因是 VRML1 场景遍历有明显的顺序状态依赖，例如 `Material`、`Coordinate3`、`Normal`、`Binding`、变换栈和 `DEF/USE`。
- 现阶段更安全的并行方向是“多文件级并行”：
  同时转换多个独立 case 或多个独立 `.wrl` 文件。
- 如果后续继续深挖单文件并行，比较现实的方向是：
  先把顶层 `Separator` 下的独立 geometry/shapes 切成批次，再做受控并行，但这需要进一步重构转换状态模型。
