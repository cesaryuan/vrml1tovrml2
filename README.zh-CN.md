# vrml1tovrml2

[默认 README](./README.md)

`vrml1tovrml2` 是一个面向 Linux 工作流的 VRML 1.0 到 VRML 2.0 转换工具，用来替代历史上的 Windows `vrml1tovrml2.exe` 流程。当前仓库提供 Rust 原生命令行实现，并保留 Python 兼容接口与模块化实现，便于继续回归和扩展。

## 项目定位

- 面向已有 `.wrl` 资产的迁移与批量转换。
- 目标是复现历史工具的可用工作流，而不是逐字节复刻原始二进制行为。
- 当前默认入口是 Rust CLI，适合直接在 Linux、WSL 和 CI 环境中使用。
- 仓库内保留了 Python 版本实现与样例数据，方便调试、对照和继续补齐兼容性。

## 当前状态

- 已可完成仓库内示例、已纳入基线比对的回归样例，以及仓库内公开 VRML 1.0 样例集的转换。
- `cargo test --test public_v1_regression` 当前已经可以通过，覆盖了已登记基线的精确输出比对，以及更大范围的“至少能成功解析并转换”的公开样例扫描。
- 已覆盖常见的 VRML 1.0 / Open Inventor 风格节点。
- 仍属于“持续补齐兼容性”的阶段，遇到历史私有扩展节点或罕见字段组合时，建议结合真实样本继续回归。

## 快速开始

### 环境要求

- Rust 工具链
- Bash
- Python 3.13+（仅在使用 Python 兼容接口或辅助脚本时需要）

如果你只使用默认的 Rust CLI，可以直接运行而不安装 Python 依赖。

如果你需要 Python 兼容接口和部分辅助脚本，再安装：

```bash
pip install -e .
```

### 直接运行

仓库根目录提供了一个便捷入口脚本，会自动调用 Rust CLI：

```bash
./vrml1tovrml2 input_v1.wrl output_v2.wrl
```

只传入输入文件时，结果会输出到标准输出：

```bash
./vrml1tovrml2 input_v1.wrl
```

打开调试日志：

```bash
./vrml1tovrml2 --verbose input_v1.wrl output_v2.wrl
```

显示读取、转换和写出的进度条：

```bash
./vrml1tovrml2 --progress input_v1.wrl output_v2.wrl
```

### 构建 Rust 可执行文件

如果你希望先编译再运行：

```bash
cargo build --release
```

## 已支持并验证的节点

- 分组与层级：`Separator`, `TransformSeparator`, `Group`, `Switch`, `LOD`
- 变换：`Translation`, `Rotation`, `Scale`, `Transform`, `MatrixTransform`
- 几何与索引：`Coordinate3`, `IndexedFaceSet`, `IndexedLineSet`, `PointSet`
- 基础体：`Cube`, `Cone`, `Cylinder`, `Sphere`
- 外观相关：`Material`, `MaterialBinding`, `Normal`, `NormalBinding`, `ShapeHints`
- 纹理相关：`Texture2`, `TextureCoordinate2`, `Texture2Transform`, `Texture2Transformation`
- 文本：`AsciiText`, `FontStyle`
- 灯光与相机：`DirectionalLight`, `PointLight`, `SpotLight`, `PerspectiveCamera`, `OrthographicCamera`
- 其他：`WWWAnchor`, `WWWInline`
- 共享定义：`DEF` / `USE`

## 示例与回归数据

示例文件位于 [examples](./examples)：

- 输入样例：[examples/sample_v1.wrl](./examples/sample_v1.wrl)
- 输出样例：[examples/sample_v2.wrl](./examples/sample_v2.wrl)
- `DEF` / `USE` 样例：[examples/sample_defs_v1.wrl](./examples/sample_defs_v1.wrl)

回归数据建议按 case 组织在 [wrl/cases](./wrl/cases) 下：

```text
wrl/
  cases/
    <case-name>/
      input.v1.wrl
      baseline.v2.from_exe.wrl
      current.v2.from_python.wrl
```

当前已整理的 case：

- [sample_minimal](./wrl/cases/sample_minimal)
- [ansys_test_from_ansys_1](./wrl/cases/ansys_test_from_ansys_1)

公开样例输入位于 [tests/data/public_v1_cases](./tests/data/public_v1_cases)，用于在不为每个外部样例都维护 golden 输出的前提下，扩大解析和转换覆盖面。

更新回归输出：

```bash
./scripts/regenerate_testset.sh
```

运行当前 Rust 回归测试：

```bash
cargo test --test public_v1_regression
```

## 仓库结构

- [vrml1tovrml2](./vrml1tovrml2)：默认命令行入口脚本
- [src](./src)：Rust CLI、解析、转换和写出实现
- [vrml1tovrml2.py](./vrml1tovrml2.py)：Python 兼容入口
- [vrml1tovrml2_pkg](./vrml1tovrml2_pkg)：Python 模块化实现
- [examples](./examples)：示例输入输出
- [wrl/cases](./wrl/cases)：回归样例与基线数据
- [tests](./tests)：Rust 集成测试与公开 VRML 1.0 样例输入
- [scripts](./scripts)：辅助脚本

## 当前限制

- 这是一个基于逆向资料和 VRML 语义重建的实现，不是对原始 DLL 的逐函数复刻。
- 目前优先覆盖主流节点，对少见的 SGI / Cosmo 风格扩展节点尚未完全补齐。
- `MatrixTransform` 目前以常见仿射场景为主，重点保留平移和轴向缩放。
- 复杂绑定关系、少见字段组合和历史兼容细节，仍需要结合真实项目样本继续验证。
- 当前已经完成部分内存优化，但整体仍不是完全“边解析边输出”的最终大文件方案。
