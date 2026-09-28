# Benchmark image: both OCR engines, the fonts the samples are rendered with.
# docker build -t anydoc-serve-bench bench/ && docker run --rm -v $PWD/bench/out:/out anydoc-serve-bench
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      fonts-noto-cjk fonts-noto-core fonts-tlwg-norasi-ttf libraqm0 libxcb1 libgl1 libglib2.0-0 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pillow numpy rapidfuzz tesserocr==2.11.0 rapidocr==3.9.2 onnxruntime==1.30.0
RUN set -e; for kind in tessdata tessdata_fast tessdata_best; do \
      mkdir -p /opt/$kind; \
      for l in eng jpn tha osd; do \
        curl -fsSL -o /opt/$kind/$l.traineddata https://github.com/tesseract-ocr/$kind/raw/4.1.0/$l.traineddata; \
      done; done
WORKDIR /bench
COPY texts.py render_samples.py ocr_bench.py ./
RUN python render_samples.py /samples
# pre-fetch every RapidOCR model the benchmark uses, so timing never includes a download
RUN BENCH_WARM_ONLY=1 python ocr_bench.py /samples /tmp/warm
CMD ["python", "ocr_bench.py", "/samples", "/out"]
