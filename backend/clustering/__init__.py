"""③ 去重聚類套件。

OUT_DIR 是本階段產物的落點（事件物件、被濾文章交接清單）——與 embedding 的
向量快取分開存放，各模組擁有自己的輸出目錄，找結果不必跨模組翻。
DB 就緒後這些檔案由 events 表與 articles.status 取代，屆時本常數可移除。
"""

import os

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
