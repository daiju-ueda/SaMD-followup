# SaMD Evidence Tracker

**製品を軸に、規制情報と英語論文を統合管理するデータ基盤**

米国（FDA）・日本（PMDA）の SaMD（Software as a Medical Device）について、承認・認証済み製品を広く収集し、英語論文を製品単位で紐づけて検索・表示できるシステムです。

## 概要

| 項目 | 内容 |
|------|------|
| 対象地域 | 米国 (FDA), 日本 (PMDA), EU (設計済み・Phase 2) |
| FDA ソース | accessdata.fda.gov bulk files (foiclass, PMA, 510(k), De Novo) |
| PMDA ソース | pmda.go.jp Excel 一覧 (承認品 + 認証品) |
| 論文ソース | PubMed, Europe PMC, OpenAlex |
| 全文取得 | Europe PMC OA, NCBI PMC OA, ローカル PMC XML |
| DB | PostgreSQL |
| UI | FastAPI + Jinja2 (ポート 8001) |
| 自動更新 | 月次 cron (毎月1日 3:00 AM) |

## アーキテクチャ

```
データ取得              正規化・リンク            表示
─────────           ─────────────          ──────
FDA bulk zips  ──┐                          
PMDA Excel     ──┼→ Product Master ──┐     Dashboard
                 │   (名寄せ・多地域統合)  │     Product List
PubMed API     ──┐                    ├──→ Product Detail
Europe PMC API ──┼→ Paper Corpus ────┤     Paper Detail
OpenAlex API   ──┘   (DOI重複排除)     │     SQL Console
                                     │
                  Scorer ────────────┘
                  (15特徴量 × 重み付け)
                  exact_product / product_family /
                  manufacturer_linked / indication_related
```

## 論文の3層分類

| 分類 | 意味 | 表示 |
|------|------|------|
| **exact_product** | 論文中に製品名が明示 | 製品固有のエビデンス |
| **manufacturer_linked** | メーカー名 + 適応が一致 | メーカー関連エビデンス |
| **indication_related** | 同疾患領域・同モダリティ | 周辺の関連論文 |

exact と related は明確に分離して表示し、誤解を防ぎます。

## セットアップ

### 前提条件

- Python 3.10+
- PostgreSQL 9.5+
- pip パッケージ: `httpx`, `pydantic`, `pydantic-settings`, `psycopg2-binary`, `fastapi`, `uvicorn`, `jinja2`, `pandas`, `openpyxl`, `beautifulsoup4`, `lxml`

### インストール

```bash
# DB 作成
sudo -u postgres createuser -s $(whoami)
sudo -u postgres createdb samd_evidence -O $(whoami)

# スキーマ適用
psql -d samd_evidence -f src/db/schema_pg95.sql

# .env 設定
cat > .env << EOF
SAMD_NCBI_API_KEY=your_key_here
SAMD_NCBI_EMAIL=your_email@example.com
EOF

# パッケージインストール
pip install httpx pydantic pydantic-settings psycopg2-binary fastapi uvicorn jinja2 pandas openpyxl beautifulsoup4 lxml
```

### 実行

```bash
# パイプライン実行（FDA + PMDA → 論文検索 → スコアリング）
python3 scripts/run_pipeline.py --fda-web --pmda-web --output data/pipeline_results.json

# DB ロード（増分 upsert）
python3 scripts/load_to_db.py

# 全文取得
python3 scripts/fetch_fulltext.py

# UI 起動
python3 -m uvicorn src.ui.app:app --host 0.0.0.0 --port 8001
```

### CLI オプション

```bash
# FDA のみ（ローカル CSV フォールバック）
python3 scripts/run_pipeline.py --skip-pmda --output data/fda_results.json

# PMDA のみ（web から Excel 取得）
python3 scripts/run_pipeline.py --skip-fda --pmda-web --output data/pmda_results.json

# 途中再開（300件目から）
python3 scripts/run_pipeline.py --resume 300 --max-products 600

# 全文取得（未取得分のみ）
python3 scripts/fetch_fulltext.py
python3 scripts/fetch_fulltext.py --retry-failed  # 失敗分も再試行
```

## プロジェクト構成

```
src/
├── bootstrap.py              # パス・.env 設定（全エントリポイント共通）
├── config/settings.py        # 環境変数ベースの設定
├── utils.py                  # 日付パース、ロギング設定
├── pipeline.py               # パイプラインオーケストレーター
│
├── ingestion/                # 製品データ取得
│   ├── fda.py                # FDA CSV パース・重複排除
│   ├── fda_scraper.py        # FDA bulk file ダウンロード (foiclass/PMA/510k/De Novo)
│   ├── pmda.py               # PMDA CSV パーサー（フォールバック用）
│   ├── pmda_scraper.py       # PMDA Excel ダウンロード（承認 + 認証）
│   ├── normalizer.py         # 製品名正規化・疾患領域/モダリティ推論
│   ├── cross_region.py       # 多地域間の製品統合（FDA ↔ PMDA）
│   └── jp_mappings.py        # メーカー名 日英マッピング
│
├── literature/               # 論文検索・取得
│   ├── query_generator.py    # 5レベルの検索クエリ自動生成
│   ├── pubmed.py             # PubMed E-utilities クライアント
│   ├── openalex.py           # OpenAlex REST API クライアント
│   ├── europe_pmc.py         # Europe PMC REST API クライアント
│   ├── fulltext.py           # 全文取得（Europe PMC / NCBI PMC OA）
│   ├── parsers.py            # 共通パーサー（abstract復元・JATS XML抽出）
│   ├── local_openalex.py     # ローカル OpenAlex スナップショット検索
│   └── local_pmc.py          # ローカル PMC XML 全文検索
│
├── linking/                  # 論文-製品リンク
│   ├── scorer.py             # 15特徴量スコアリング・5段階分類
│   └── deduplicator.py       # DOI/PMID ベースの論文重複排除
│
├── models/                   # Pydantic ドメインモデル
│   ├── product.py            # Product, RegulatoryEntry, ProductAlias
│   ├── paper.py              # Paper, PaperAuthor
│   └── linking.py            # ProductPaperLink, スコアリング設定
│
├── db/                       # データベース層
│   ├── schema_pg95.sql       # PostgreSQL スキーマ
│   ├── connection.py         # 接続管理
│   └── repositories.py       # Product/Paper/Stats リポジトリ（upsert対応）
│
└── ui/                       # Web UI
    ├── app.py                # FastAPI アプリケーション
    └── templates/            # Jinja2 テンプレート

scripts/
├── run_pipeline.py           # パイプライン CLI
├── load_to_db.py             # DB ロード（増分 upsert）
├── fetch_fulltext.py         # 全文取得バッチ
└── monthly_update.sh         # 月次自動更新 cron スクリプト
```

## データソース

### FDA（米国）

| ソース | URL | 内容 |
|--------|-----|------|
| foiclass.zip | accessdata.fda.gov/premarket/ftparea/ | 製品分類 → SaMD コード導出 |
| pma.zip | 同上 | PMA 承認 |
| pmnlstmn.zip | 同上 | 510(k) 月次クリアランス |
| De Novo DB | accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/denovo.cfm | De Novo 認可 |

### PMDA（日本）

| ソース | URL | 内容 |
|--------|-----|------|
| 承認品目 Excel | pmda.go.jp (プログラム医療機器の製造販売承認品目一覧) | クラスIII/IV 承認品 |
| 認証品目 Excel | pmda.go.jp (認証品目リスト) | クラスII 認証品（SaMD キーワードフィルタ） |

## スコアリング

15 の特徴量に重み付けスコアを計算し、閾値で分類:

| 特徴量 | 重み | 説明 |
|--------|------|------|
| product_name_in_title | 30 | タイトルに製品名 |
| product_name_in_abstract | 20 | 抄録に製品名 |
| regulatory_id_in_text | 25 | 510(k)番号等が出現 |
| product_alias_in_title | 20 | 別名がタイトルに |
| product_alias_in_abstract | 15 | 別名が抄録に |
| manufacturer_in_affiliation | 8 | 著者所属にメーカー名 |
| ... | | 他10特徴量 |

**False positive 対策**: 一般語の製品名（Rapid, HALO 等）は、メーカー名共起または規制ID確認がない限り `indication_related` に降格し、`human_review_needed=true` を設定。

## 月次自動更新

```
cron: 0 3 1 * *   毎月1日 AM 3:00

1. PMDA: Excel ダウンロード（承認 + 認証）→ 論文検索
2. FDA: bulk file ダウンロード → 論文検索（300件バッチ）
3. DB 増分更新（upsert — human review・全文を保持）
4. 全文取得（未取得分のみ）
```

## ライセンス

MIT
