# lyrics-metaphor-world-analysis

日本語歌詞の比喩と語彙世界を精読し、その結果をセクションの働きや反復による変化へつなぐ Codex スキルです。標準対象は1〜5曲。本文の観察、解釈、未確定点を分け、曲番号・行番号から根拠を確認できる分析を行います。

## できること

- 文脈義と基本義を比べ、比喩・換喩・字義的表現を検討する。
- 語彙場、比喩の対応関係、人物・時空・願望や記憶などの歌詞世界を整理する。
- 隣接区間、再登場する区間、異なるタグの対応表現を比較する。
- 変化・維持・循環・対置・未決着を本文の根拠から説明する。

対象は歌詞テキストです。音楽・歌唱・MVからの補完、歌詞の書き起こし、作家全体の作風の断定、特定作家の表現の模倣は扱いません。

## Codex で使う

このフォルダー全体を、ユーザー用の `~/.agents/skills/lyrics-metaphor-world-analysis/`、または利用するプロジェクトの `.agents/skills/lyrics-metaphor-world-analysis/` に配置してください。`SKILL.md` と同じ階層に `references/`、`scripts/`、`agents/` を保ちます。表示されない場合は Codex を再起動してください。

スキルの配置先については [OpenAI の公式ガイド](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills) を参照してください。

歌詞を添付・貼り付けて、次のように依頼します。

```text
$lyrics-metaphor-world-analysis を使い、添付歌詞の比喩と語彙世界を分析した後、その結果に対応するセクション分析をしてください。
```

原文の表記・改行・空行・セクションタグを残してください。複数曲は提供順で番号を付け、曲内行番号はタグのみの行を除いて空白行も数えます。詳しい規則は [報告形式と境界例](references/output-and-examples.md) にあります。

## ファイル構成

| ファイル | 内容 |
|---|---|
| [SKILL.md](SKILL.md) | 全体の分析手順と監査項目 |
| [agents/openai.yaml](agents/openai.yaml) | 表示名と呼び出し例 |
| [references/identification.md](references/identification.md) | 語句の抽出・比喩判定 |
| [references/world-analysis.md](references/world-analysis.md) | 語彙の関係と歌詞世界 |
| [references/section-analysis.md](references/section-analysis.md) | セクションの機能・接続・回帰 |
| [references/output-and-examples.md](references/output-and-examples.md) | 報告形式、行番号、架空例、索引の使い方 |
| [references/sources.md](references/sources.md) | 参考文献、設計時の閲覧範囲、応用上の限界 |
| [scripts/index_lyrics.py](scripts/index_lyrics.py) | 原文を保持する任意の索引ツール |
| [tests/test_index_lyrics.py](tests/test_index_lyrics.py) | 索引ツールのテスト |

## 任意の索引ツール

Python 3 の標準ライブラリだけで動作します。分析手順を読むだけなら Python は不要です。以下はこのフォルダーを作業場所とし、出力先の `work/` を作成済みの場合の例です。

```text
python scripts/index_lyrics.py lyrics.txt --output work/lyrics-index.json
python scripts/index_lyrics.py corpus.txt --song-map work/song-map.json --output work/lyrics-index.json
```

最初のコマンドは原文索引だけを作ります。報告用の曲番号・曲内行番号には、本文範囲を確認した `--song-map` が必要です。索引ツールは比喩や話者を自動判定しません。[詳細な使用方法](references/output-and-examples.md#任意の本文索引ツール)を参照してください。

テストは次のコマンドで実行できます。

```text
python -B -m unittest discover -s tests -v
```

## 手法について

MIPVU、概念メタファー、Text World Theory などを参考にした精読プロトコルです。標準 MIPVU の完全実施、心理効果の実証、分析精度の保証を主張するものではありません。理論の名称より、個々の表現と関係の根拠を優先します。出典と適用の限界は [参考文献](references/sources.md) に記載しています。

このリポジトリには、既存楽曲の歌詞コーパスを含めていません。説明とテストには架空の短文を使っています。
