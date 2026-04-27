# vrml1tovrml2

Linux-native reimplementation of the old Windows `vrml1tovrml2.exe` workflow.

这个仓库原先只有老的 Windows 二进制和你导出的逆向资料。现在补上了一个可直接在 Linux 上运行的命令行实现：

- 可执行入口：`./vrml1tovrml2`
- Python 实现：`./vrml1tovrml2.py`
- 示例输入：`examples/sample_v1.wrl`
- 示例输出：`examples/sample_v2.wrl`

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
```

两个示例都能成功生成 `#VRML V2.0 utf8` 输出。

## 当前限制

- 这是一个“按逆向资料和 VRML 语义重建”的 Linux 实现，不是逐函数逐字节复刻原 DLL。
- 目前优先覆盖主流 VRML 1.0/Open Inventor 风格节点，对一些非常少见的 SGI/Cosmo 专有扩展节点还没有完整补齐。
- `MatrixTransform` 目前做的是常见仿射场景下的近似转换，重点保留平移和轴向缩放。
- 复杂绑定、少见字段组合、历史兼容细节，仍建议拿你的真实 `.wrl` 样本继续回归补全。

## 代码说明

- 解析、转换、输出都在 [vrml1tovrml2.py](/home/cesar/vrml1tovrml2/vrml1tovrml2.py)。
- 代码里在关键路径补了必要日志和简短注释，便于继续对照逆向资料扩展。
