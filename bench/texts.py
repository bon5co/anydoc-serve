"""Ground-truth texts for the OCR benchmark.

Japanese page 1 is the opening of Natsume Soseki's "I Am a Cat" (1905, public
domain, text from Aozora Bunko). English page 1 is the Gettysburg Address
(1863, public domain). Every other page is written for this benchmark and is
MIT-licensed with the repository.
"""

PAGES: dict[str, list[str]] = {
    "jpn": [
        "吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。"
        "何でも薄暗いじめじめした所でニャーニャー泣いていた事だけは記憶している。"
        "吾輩はここで始めて人間というものを見た。しかもあとで聞くとそれは書生という"
        "人間中で一番獰悪な種族であったそうだ。この書生というのは時々我々を捕えて"
        "煮て食うという話である。しかしその当時は何という考もなかったから別段恐しい"
        "とも思わなかった。ただ彼の掌に載せられてスーと持ち上げられた時何だか"
        "フワフワした感じがあったばかりである。",
        "請求書\n"
        "株式会社サンプル商事 御中\n"
        "発行日：2026年9月28日　請求番号：INV-2026-0042\n"
        "下記の通りご請求申し上げます。お支払い期限は2026年10月31日です。\n"
        "品目：文書変換サービス（月額プラン）　数量：1　単価：12,000円\n"
        "品目：光学文字認識オプション　数量：3　単価：4,500円\n"
        "小計：25,500円　消費税（10％）：2,550円　合計金額：28,050円\n"
        "振込先：みずほ銀行　東京支店　普通預金　1234567\n"
        "ご不明な点がございましたら、担当者までお問い合わせください。",
    ],
    "tha": [
        "ประกาศบริษัท เรื่อง การปรับปรุงระบบเอกสารภายในองค์กร "
        "เพื่อให้การทำงานมีประสิทธิภาพมากขึ้น บริษัทจะเปลี่ยนมาใช้ระบบจัดเก็บเอกสารแบบดิจิทัล"
        "ตั้งแต่วันที่ 1 ตุลาคม 2569 เป็นต้นไป พนักงานทุกคนต้องสแกนเอกสารสำคัญ"
        "และอัปโหลดเข้าสู่ระบบภายในสามสิบวัน หากมีข้อสงสัยกรุณาติดต่อฝ่ายเทคโนโลยีสารสนเทศ "
        "โทร 02-123-4567 ในวันและเวลาทำการ",
        "ภาษาไทยเป็นภาษาที่มีวรรณยุกต์ห้าเสียง ได้แก่ เสียงสามัญ เสียงเอก เสียงโท "
        "เสียงตรี และเสียงจัตวา การเขียนภาษาไทยไม่มีการเว้นวรรคระหว่างคำ "
        "แต่จะเว้นวรรคเมื่อจบประโยคหรือความคิด ซึ่งทำให้การตัดคำเป็นปัญหาสำคัญ"
        "ของการประมวลผลภาษาธรรมชาติ นักวิจัยหลายคนจึงพัฒนาเครื่องมือตัดคำขึ้นมา"
        "เพื่อช่วยให้คอมพิวเตอร์เข้าใจข้อความภาษาไทยได้ถูกต้องยิ่งขึ้น",
    ],
    "eng": [
        "Four score and seven years ago our fathers brought forth on this continent, "
        "a new nation, conceived in Liberty, and dedicated to the proposition that all "
        "men are created equal. Now we are engaged in a great civil war, testing whether "
        "that nation, or any nation so conceived and so dedicated, can long endure. We are "
        "met on a great battle-field of that war. We have come to dedicate a portion of "
        "that field, as a final resting place for those who here gave their lives that "
        "that nation might live. It is altogether fitting and proper that we should do this.",
        "INVOICE\n"
        "Acme Widgets Ltd., 42 Example Street, Springfield\n"
        "Invoice number: INV-2026-0042   Date: 28 September 2026\n"
        "Bill to: Example Corporation, Accounts Payable\n"
        "Document conversion service (monthly plan)   1 x $120.00   $120.00\n"
        "Optical character recognition add-on   3 x $45.00   $135.00\n"
        "Subtotal: $255.00   Tax (10%): $25.50   Total due: $280.50\n"
        "Payment is due within 30 days. Please quote the invoice number with your payment.",
    ],
}
