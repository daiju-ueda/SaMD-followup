# SaMD Evidence Tracker — Design Document

## 1. 全体方針

### 1.1 なぜ Product Master 先行か

論文を先に集めると「何の製品の論文か」の帰属が曖昧になる。
製品マスターを先に構築し、製品ごとに正規化された名称・別名辞書・メーカー名・intended use を持たせることで、
論文検索クエリを高精度に自動生成でき、hit した論文の帰属判定も機械的に行える。

逆に論文先行だと:
- 製品名揺れの吸収ができない（IDx-DR vs IDx DR vs Digital Diagnostics IDx-DR）
- 同一製品の複数バージョンを区別できない
- メーカー改名（例: Arterys → Tempus Radiology）に対応できない

### 1.2 なぜ米国 + 日本を MVP にするか

| 基準 | 米国 | 日本 | EU |
|------|------|------|----|
| 公的製品リスト | FDA 510(k)/De Novo/PMA DB + AI/ML list | PMDA 承認/認証品一覧 | EUDAMED（未完全公開） |
| API 有無 | openFDA REST API あり | API なし（HTML/PDF スクレイピング） | 公開 API 限定的 |
| データ品質 | 高（構造化） | 中（日本語、一部非構造化） | 低（分散、不完全） |
| 製品数 | 最多（1000+） | 中規模（200+） | 把握困難 |

米国は最もデータアクセスが容易かつ製品数が多い。
日本は API がないが PMDA の公開情報から構造化可能で、かつ日本市場固有の SaMD がある。
EU は EUDAMED の公開範囲が限定的で、Notified Body の証明書が分散しており、Phase 2 で段階的に対応するのが現実的。

### 1.3 なぜ Exact と Related を分けるか

SaMD のエビデンス評価において最大のリスクは「この製品のエビデンスがある」と誤認させること。

- **Exact product evidence**: その製品の臨床性能を直接示す論文。規制当局や医療機関の意思決定に直結
- **Manufacturer-linked**: 同じメーカーの類似技術だが、当該製品そのものかは不明確。参考情報として有用
- **Indication-related**: 同じ疾患領域の論文。技術的文脈の理解に有用だが、当該製品の性能とは無関係

これらを混ぜて表示すると:
- 規制上のエビデンス要件との混同
- 購買意思決定の誤り
- 信頼性の毀損

---

## 2. システムアーキテクチャ

```
┌─────────────────────────────────────────────────────────────────┐
│                        Data Sources                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐   │
│  │ FDA      │  │ PMDA     │  │ EUDAMED  │  │ Literature    │   │
│  │ openFDA  │  │ Website  │  │ (Ph.2)   │  │ PubMed        │   │
│  │ 510k/PMA │  │ 承認/認証 │  │ NB certs │  │ Europe PMC    │   │
│  │ AI/ML    │  │          │  │          │  │ OpenAlex      │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬───────┘   │
│       │              │              │                │           │
└───────┼──────────────┼──────────────┼────────────────┼───────────┘
        │              │              │                │
        ▼              ▼              ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Ingestion Layer                               │
│  ┌──────────────┐  ┌─────────────┐  ┌────────────────────────┐  │
│  │ FDA Ingester │  │ PMDA        │  │ Literature Fetcher     │  │
│  │ (API-based)  │  │ Ingester    │  │ (PubMed/PMC/OpenAlex)  │  │
│  │              │  │ (Scraper)   │  │                        │  │
│  └──────┬───────┘  └──────┬──────┘  └───────────┬────────────┘  │
│         │                 │                      │               │
└─────────┼─────────────────┼──────────────────────┼───────────────┘
          │                 │                      │
          ▼                 ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Normalization Layer                            │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Product Normalizer                                      │    │
│  │ - Name deduplication & alias resolution                 │    │
│  │ - Manufacturer name normalization                       │    │
│  │ - Regulatory status mapping                             │    │
│  │ - Disease area / modality tagging                       │    │
│  └─────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Paper Normalizer                                        │    │
│  │ - DOI deduplication                                     │    │
│  │ - Author affiliation extraction                         │    │
│  │ - Study type classification                             │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
          │                                        │
          ▼                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                     PostgreSQL Database                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ products     │  │ papers       │  │ product_paper_links  │   │
│  │ product_     │  │ paper_       │  │ link_scores          │   │
│  │   aliases    │  │   authors    │  │ search_queries       │   │
│  │ product_     │  │              │  │ review_queue         │   │
│  │   regulatory │  │              │  │                      │   │
│  └──────────────┘  └──────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Linking & Scoring Engine                      │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Query Generator → Search → Candidate Retrieval →        │    │
│  │ Feature Extraction → Classification → Scoring →          │    │
│  │ Human Review Queue                                      │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                         API Layer (FastAPI)                      │
│  GET /products                                                  │
│  GET /products/{id}                                             │
│  GET /products/{id}/papers                                      │
│  GET /papers/{id}                                               │
│  GET /search?q=...                                              │
│  GET /products/{id}/evidence-summary                            │
│  POST /admin/ingest/fda                                         │
│  POST /admin/ingest/pmda                                        │
│  POST /admin/link-papers                                        │
│  GET /admin/review-queue                                        │
│  POST /admin/review/{link_id}                                   │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (Phase 2+)                     │
│  Product list → Product card → Papers panel                     │
│  Faceted search by region / disease / modality                  │
│  Evidence gap dashboard                                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. データソース戦略

### 3.1 米国 (FDA)

**Primary Sources:**
1. **openFDA API** (`api.fda.gov/device/510k`, `/device/pma`, `/device/classification`)
   - 510(k) clearances, PMA approvals をクエリ
   - Product code + advisory committee で SaMD 候補をフィルタ
2. **FDA AI/ML-Enabled Medical Devices List** (公開 PDF/Excel)
   - FDAが公式に管理するAI/ML SaMD リスト
   - 最も信頼性の高い SaMD 特定ソース
3. **De Novo Database** (FDA website)
   - AI/ML SaMD の多くが De Novo pathway を利用

**SaMD 抽出ルール:**
- Product code が QAS, QBS, QDQ, QFM, QMT, POK, QIH 等の software-related codes
- Advisory committee が Radiology (LI), Cardiovascular (DT) 等の AI/ML 多発領域
- AI/ML list に掲載されている device
- Device description に "software", "algorithm", "artificial intelligence", "machine learning", "SaMD", "computer-aided" を含む
- Standalone software として分類されている（combination device ではない）

**Regulatory Pathway 保持:**
```
510(k) → cleared
De Novo → granted (authorized)
PMA → approved
Breakthrough Device → designation (pathway ではなく優先審査指定)
```

### 3.2 日本 (PMDA)

**Primary Sources:**
1. **PMDA 承認品目一覧** (`www.pmda.go.jp`)
   - 医療機器承認品（クラスIII/IV）
2. **PMDA 認証品目** (第三者認証機関経由)
   - クラスII 認証品
3. **PMDA SaMD 専用ページ** (存在する場合)
4. **厚生労働省 薬事承認情報**

**日本語→英語マッピング方針:**
- 製品名: メーカー公式英語名がある場合はそれを使用。ない場合は transliteration + manual mapping
- メーカー名: 英語法人名を正とする（例: オリンパス → Olympus Corporation）
- 一般的名称: JIS/JMDN コードから GMDN (Global Medical Device Nomenclature) へのマッピングを利用
- 適応症: MedDRA 日本語版 → MedDRA 英語版の対応表を利用

**Regulatory Status 表現:**
```
承認 (クラスIII/IV) → approved (PMDA)
認証 (クラスII) → certified (Third-party certification body)
届出 (クラスI) → notified (対象外とするが将来拡張可)
```

### 3.3 EU (Phase 2)

**課題:**
- EUDAMED はまだ完全公開されていない（actor registration module のみ公開、device registration module は段階的）
- CE marking 情報は Notified Body ごとに分散
- MDR/IVDR 移行期で regulatory status が流動的

**Phase 2 での approach:**
1. EUDAMED 公開部分からの device 登録情報取得
2. Notified Body (BSI, TÜV, etc.) の certificate database からの収集
3. Manufacturer の DoC (Declaration of Conformity) 公開情報
4. 業界団体 (MedTech Europe) のデータベース

**Regulatory Status 表現:**
```
CE marked (MDD) → legacy_ce_marked
CE marked (MDR) → mdr_ce_marked
CE certificate expired → certificate_expired
In MDR transition → mdr_transition
```

**Evidence Tier (EUデータ品質):**
```
tier_1: EUDAMED 公式データ
tier_2: Notified Body certificate 確認済み
tier_3: Manufacturer 自己申告 (DoC/website)
tier_4: Secondary source (industry database, news)
```

---

## 4. Product Master データモデル

### 4.1 正規化方針

1つの物理的製品が複数地域で異なる名称・regulatory status を持つ場合がある。
例: IDx-DR (米国 De Novo) = IDx-DR (EU CE marked) = 同一アルゴリズムの日本未承認品

設計方針:
- `products` テーブルは **canonical product** を表す（地域横断の論理エンティティ）
- `product_regulatory_entries` テーブルが **地域ごとの regulatory record** を保持
- 1 product : N regulatory_entries の関係

これにより:
- 同一製品の多地域展開を1つの product_id で管理
- 地域ごとの regulatory status を正確に保持
- 論文紐付けは product_id 単位で行う

### 4.2 別名辞書

製品名の揺れ吸収が論文検索精度を決定する最重要因子。

別名の種類:
- `trade_name`: 商品名（地域別に異なる場合あり）
- `product_family`: 製品ファミリー名（例: Viz.ai → Viz LVO, Viz CTP, Viz PE）
- `former_name`: 旧名称（メーカー改名、製品改名）
- `abbreviation`: 略称
- `regulatory_name`: 規制文書上の名称（しばしば商品名と異なる）
- `generic_name`: 一般的名称
- `japanese_name`: 日本語名（日本製品の場合）

---

## 5. 論文検索設計

### 5.1 検索クエリ自動生成

製品ごとに以下の検索式テンプレートを生成:

**Level 1: Exact product search**
```
("product_name" OR "alias1" OR "alias2") AND (software OR device OR algorithm)
```

**Level 2: Product family search**
```
("product_family") AND ("disease_area" OR "intended_use")
```

**Level 3: Manufacturer + indication search**
```
("manufacturer_name" OR "manufacturer_alias") AND ("intended_use" OR "disease_area") AND (algorithm OR "artificial intelligence" OR "machine learning" OR software OR "computer-aided")
```

**Level 4: Regulatory ID search**
```
("510k_number" OR "PMA_number" OR "De_Novo_number")
```

**Level 5: Indication-level search**
```
("disease_area") AND ("modality") AND (algorithm OR AI OR "machine learning" OR "deep learning" OR "computer-aided") AND (validation OR performance OR accuracy OR "clinical trial")
```

### 5.2 検索対象

| Source | API | Rate Limit | 特徴 |
|--------|-----|-----------|------|
| PubMed | E-utilities (NCBI) | 10 req/s with API key | 最も網羅的な医学文献DB |
| Europe PMC | REST API | 制限緩い | Full-text search 可能、OA論文本文あり |
| OpenAlex | REST API | 100k req/day | DOI/概念ベースの検索、citation graph |

### 5.3 検索順序

1. PubMed で Level 1-4 を実行（product-specific）
2. Europe PMC で Level 1-3 を実行（full-text hit 狙い）
3. OpenAlex で Level 1-3 を実行 + cited-by / references の展開
4. Level 5 は indication-related として別扱い
5. 全ソースの結果を DOI ベースで deduplicate

---

## 6. Product-Paper Linking & Scoring

### 6.1 Classification

```
exact_product     : 論文に製品名/別名が明示的に出現
product_family    : 製品ファミリー名が出現するが当該バージョンは不明
manufacturer_linked: メーカー名 + 適応 + modality が一致
indication_related: 同じ disease area + modality だが製品固有情報なし
irrelevant        : false positive（除外）
```

### 6.2 Scoring Features

| Feature | Weight | Description |
|---------|--------|-------------|
| product_name_in_title | 30 | タイトルに製品名 exact hit |
| product_name_in_abstract | 20 | 抄録に製品名 exact hit |
| product_name_in_fulltext | 10 | 本文に製品名 hit (full-text 利用可の場合) |
| product_alias_hit | 15 | 別名でのhit |
| product_family_hit | 10 | ファミリー名でのhit |
| manufacturer_in_author_affiliation | 8 | 著者所属にメーカー名 |
| manufacturer_in_text | 5 | テキストにメーカー名 |
| intended_use_match | 5 | intended use キーワード一致 |
| disease_area_match | 3 | disease area 一致 |
| modality_match | 3 | modality 一致 |
| regulatory_id_hit | 25 | 510(k)番号等が論文中に出現 |
| study_type_clinical | 5 | clinical validation/trial study |
| study_type_multicenter | 3 | multicenter study |

### 6.3 Classification Rules

```python
score >= 50 AND product_name_hit → exact_product
score >= 30 AND product_family_hit → product_family
score >= 20 AND manufacturer_hit AND indication_match → manufacturer_linked
score >= 10 AND indication_match → indication_related
score < 10 → irrelevant (discard)

# Ambiguous zone
20 <= score < 50 AND no product_name_hit → human_review_needed = True
```

### 6.4 Human Review

以下の条件で human_review_needed = True:
- Classification が曖昧（score が閾値付近）
- 製品名が一般的な単語と重複（例: "Guardian", "Vision"）
- メーカー名のみで hit し、製品名が見つからない
- 複数製品が同一論文に該当する場合

---

## 7. API 設計

### 7.1 Endpoints

```
GET  /api/v1/products                    # 製品一覧（pagination, filter, sort）
GET  /api/v1/products/{product_id}       # 製品詳細 + regulatory entries
GET  /api/v1/products/{product_id}/papers # 製品に紐づく論文（分類別）
GET  /api/v1/products/{product_id}/evidence-summary  # エビデンス要約
GET  /api/v1/papers/{paper_id}           # 論文詳細
GET  /api/v1/search                      # 全文検索（products + papers）

# Admin
POST /api/v1/admin/ingest/{source}       # データ取り込み実行
POST /api/v1/admin/search-papers/{product_id}  # 論文検索実行
GET  /api/v1/admin/review-queue          # Human review キュー
POST /api/v1/admin/review/{link_id}      # レビュー結果登録
GET  /api/v1/admin/stats                 # システム統計
```

### 7.2 Query Parameters (products)

```
region: us | jp | eu
disease_area: string
modality: string
manufacturer: string
regulatory_pathway: string
has_exact_evidence: bool
sort_by: name | date | evidence_count
page: int
per_page: int
```

---

## 8. UI 表示モデル

### 8.1 Product Card

```json
{
  "product_id": "prod_001",
  "canonical_name": "IDx-DR",
  "manufacturer": "Digital Diagnostics (formerly IDx Technologies)",
  "intended_use": "Autonomous detection of diabetic retinopathy",
  "disease_area": "Ophthalmology - Diabetic Retinopathy",
  "modality": "Fundus Photography",
  "standalone_samd": true,
  "regulatory_entries": [
    {
      "region": "us",
      "pathway": "De Novo",
      "status": "authorized",
      "regulatory_id": "DEN180001",
      "date": "2018-04-11",
      "source_url": "https://www.accessdata.fda.gov/..."
    }
  ],
  "evidence_summary": {
    "exact_product": 47,
    "product_family": 3,
    "manufacturer_linked": 12,
    "indication_related": 230,
    "evidence_gap": "No prospective multicenter RCT found"
  }
}
```

### 8.2 Papers Section

Exact / Manufacturer-linked / Indication-related を明確にタブまたはセクション分け。

各論文:
```json
{
  "paper_id": "paper_12345",
  "title": "Pivotal Trial of an Autonomous AI-Based Diagnostic System...",
  "authors": ["Abramoff MD", "Lavin PT", "..."],
  "journal": "NPJ Digital Medicine",
  "year": 2018,
  "doi": "10.1038/s41746-018-0040-6",
  "pmid": "30137485",
  "link_type": "exact_product",
  "confidence_score": 0.95,
  "matched_terms": ["IDx-DR", "De Novo", "DEN180001"],
  "study_tags": ["pivotal_trial", "prospective", "multicenter", "fda_submission"],
  "human_reviewed": true
}
```

---

## 9. ロードマップ

### MVP (Phase 1) — 8-10 weeks
- FDA product ingestion (openFDA + AI/ML list)
- PMDA product ingestion (scraping + manual curation)
- Product master with alias dictionary
- PubMed literature search + query generation
- Basic product-paper linking with scoring
- FastAPI backend
- Product list / detail / papers API
- Human review queue
- 対象: ~1000 US + ~200 JP products

### Phase 2 — 6-8 weeks
- EU product ingestion (EUDAMED + NB certs)
- Europe PMC + OpenAlex integration
- Full-text search (OA papers)
- Author affiliation matching
- Frontend UI (React/Next.js)
- Evidence gap analysis
- Confidence score calibration with human review data

### Phase 3 — 4-6 weeks
- Citation graph analysis (OpenAlex)
- Automated periodic re-ingestion (cron)
- ML-based classification refinement (train on human review data)
- Export (CSV, regulatory dossier format)
- Alerting (new papers for tracked products)
- Multi-language support for UI

---

## 10. 想定される課題と回避策

| 課題 | 影響 | 回避策 |
|------|------|--------|
| 製品名が一般的単語と重複 | False positive 大量発生 | 2-gram 以上の検索 + context filter + human review |
| メーカー改名・買収 | 旧名での検索漏れ | manufacturer_aliases テーブルで全履歴保持 |
| openFDA で SaMD を正確に抽出できない | 漏れ or ノイズ | AI/ML list を primary、openFDA を補完として使用 |
| PMDA のサイト構造変更 | Scraper 破損 | Scraper にモニタリング + fallback (manual import CSV) |
| 論文の full-text がない | Scoring 精度低下 | Abstract-only scoring を default、full-text は bonus |
| EUDAMED データ不完全 | EU 製品の網羅性低い | evidence_tier で信頼度を明示、manufacturer 直接情報で補完 |
| Rate limiting (PubMed etc.) | 取得速度制限 | Queue-based async processing + respectful rate limiting |
| 同一製品の multi-region 統合 | 重複 product 発生 | Manufacturer + product name fuzzy match → manual confirm |
