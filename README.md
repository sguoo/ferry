# Ferry

YouTube 다운로더 Windows 데스크톱 앱 (Python 3.12 + PySide6 + yt-dlp). 링크 붙여넣기·검색으로 영상을 받고, 재생목록/채널을 일괄로 받고, PC 폴더의 미디어로 `.m3u8` 재생목록을 만들고, 받은 파일을 라이브러리에서 관리합니다.

## 실행

```powershell
uv sync
uv run main.py

# 같은 백엔드를 쓰는 CLI
uv run youtube_download.py search "lofi jazz" -n 5
uv run youtube_download.py info <url>
uv run youtube_download.py playlist <url>
uv run youtube_download.py get <url> [--audio] [--quality 1080] [--sub ko]
```

- ffmpeg가 PATH에 있거나 환경설정에서 경로를 지정해야 병합/오디오 변환이 됩니다 (없으면 사이드바에 `NO FFMPEG`).
- 기본 1280×800, 최소 1000×660. 프레임리스 창이며 타이틀바 드래그 / 더블클릭(최대화) / 가장자리 리사이즈(Win32 `WM_NCHITTEST`)를 지원합니다.
- 설정은 `settings.json`(프로젝트 폴더)에 저장되고, 썸네일 캐시는 `cache/thumbs/`에 쌓입니다.

## 기능

| 화면 | 동작 |
|---|---|
| **다운로더 · 링크** | 재생목록/채널 링크(list=, /playlist, /@채널)를 넣으면 재생목록 패널이 떠서 재생목록 전체 다운로드 (N개)로 한 번에 큐에 넣거나, 항목 골라서 받기(재생목록 화면), 이 영상만을 고를 수 있습니다. 영상 링크는 붙여넣기(또는 Enter) → 즉시 분석 → 화질 타일(해상도·fps·코덱·예상 용량), 비디오 코덱 선택, 자막 트랙 체크, 저장 경로 → `지금 다운로드`(즉시) / `대기열 추가`(슬롯 나면 시작). Mode 세그먼트로 비디오·MP3·자막만 전환. |
| **다운로더 · 검색** | 제목·채널·키워드로 검색(종류·정렬 필터) → 각 결과에서 `다운로드`(현재 Mode·기본 화질로 즉시), `링크 복사`, 슬라이더 아이콘(화질 골라 받기). 재생목록 결과는 재생목록 화면으로 넘어갑니다. |
| **진행 중인 작업** | 진행률·속도·남은 시간·조각 수·ffmpeg 단계 실시간 표시. 일시중지(.part 보존, 재개 시 이어받기), 폴더 열기, 취소/제거, 모두 일시중지, 완료 항목 정리. 동시 실행 수는 환경설정의 `동시 다운로드`. |
| **재생목록 · 링크** | 재생목록/채널 주소 분석 → 항목 표(체크, 썸네일, 채널, 길이, 행별 프리셋). 전체 선택, 범위 지정, 일괄 프리셋 적용, 비디오/음원만 전환 → `일괄 다운로드 시작`이 선택 항목을 작업 큐에 넣고 행마다 진행률/상태를 표시. 저장 위치는 `<저장 폴더>/<재생목록 이름>/`. |
| **재생목록 · 폴더** | 폴더 찾아보기(하위 폴더 포함) → 미디어 파일 표(ffprobe로 길이·해상도·비트레이트, 영상은 프레임 썸네일). 정렬(자연/수정일/길이/크기), 전체·비디오·음원 필터, 선택 반전 → `재생목록 파일로 저장`이 선택 파일로 `.m3u8` 작성(상대 경로 옵션). 기존 `.m3u8` 불러오기도 됩니다. |
| **라이브러리** | 저장 폴더를 재귀 스캔해 카드 그리드로 표시(프레임 썸네일, 용량, 화질, 코덱). 파일명 검색, 전체/비디오/음악/4K 칩, 앱 안에서 재생 / 전체 재생(보이는 항목 순서대로), 외부 플레이어로 열기, 탐색기에서 보기. 다운로드가 끝나면 자동으로 다시 스캔. |
| **플레이어** | 앱 안에서 영상·음원을 재생하는 오버레이(QtMultimedia FFmpeg 백엔드 → MP4/MKV/WebM/AV1/VP9/Opus 등). 큐 재생(이전/다음, 끝나면 자동 다음), 탐색 슬라이더, 음량·음소거, 재생 속도 0.5–2x, 전체 화면. 라이브러리 카드, 완료된 다운로드 행, 폴더 재생목록 행(이 파일부터 재생 / 선택 항목 재생)에서 열립니다. **자막**: 영상 옆의 `.srt`/`.vtt`/`.srv3`를 자동으로 찾아 영상 위에 표시하고(자막을 임베드해도 `.srt`는 남겨 둠), 내장 자막 트랙도 선택할 수 있습니다. 자막을 받을 때 YouTube 원본 `.srv3`도 함께 저장하며, 이 파일이 있으면 유튜브에서 보던 그대로 글자 색·외곽선·크기·굵기·화면 위치·정렬(여러 자막 창 동시 표시, 33ms 단위 색 변화)을 재현합니다(콤보에 `· 원본 스타일`). 영상은 `QGraphicsVideoItem`으로 그려 자막·컨트롤이 항상 영상 위에 보입니다(설정의 기본 자막 언어를 먼저 고름, S 키로 순환). 단축키: Space, ←→, ↑↓, M, N/P, S, F, Esc. |
| **미니 재생바** | 재생 중 다른 화면으로 이동하면(환경설정 다른 화면에서도 계속 재생 켬, 기본값) 플레이어가 창 하단의 미니 재생바로 줄어들고 재생은 계속됩니다: 제목/순번, 이전·재생/정지·다음, 탐색 슬라이더, 음량, 플레이어 열기, 종료. 설정을 끄면 화면 이동 시 재생을 멈춥니다. |
| **자막 뷰어** | 자막 파일의 모든 큐를 시간순 목록으로 보여주는 오버레이. 언어 전환, 내용 검색, 재생 중이면 현재 큐 하이라이트, 큐 클릭 → 플레이어가 그 시점으로 이동. 라이브러리 카드의 `자막 ko, en` 버튼, 자막만 받은 항목의 카드(`자막` 칩), 완료된 자막 다운로드 행에서 열립니다. |
| **환경설정** | 저장 폴더, 파일명 템플릿, 기본 화질·컨테이너·자막 언어, 동시 다운로드, 속도 제한, 클립보드 감지, 다른 화면에서도 계속 재생, 완료 알림/사운드, ffmpeg 경로(즉시 검증), 브라우저 쿠키. 저장 시 검증 실패 항목은 인라인 오류로 표시. |
| **전역** | 클립보드에 YouTube 링크가 복사되면 자동으로 다운로더(또는 재생목록)에 불러옵니다. 다운로드 완료 시 트레이 알림. 사이드바에 저장 드라이브 여유 공간, 합산 다운로드 속도, yt-dlp/ffmpeg 상태. |

## 구조

```
main.py                 진입점 (QApplication, 폰트·스타일시트, context.init)
youtube_download.py     CLI (app.youtube 사용)
app/
  youtube.py            yt-dlp 백엔드 (Qt 무관): fetch_info / search / fetch_playlist / download(진행률·취소) / fetch_thumbnail + 표시용 포맷터
  local.py              폴더 스캔(ffprobe, 자막 사이드카 매칭), .m3u8 읽기/쓰기, 프레임 썸네일, 탐색기 열기
  subtitles.py          SRT/WebVTT 파싱, 시점별 큐 조회, 사이드카/언어 판별
  workers.py            QThreadPool 태스크 + 큐 커넥션 시그널 (Task.on / call / DownloadTask)
  jobs.py               Job / JobManager: 다운로드 큐, 동시 실행 제한, 일시중지·재개, 속도 집계
  settings.py           Settings dataclass ↔ settings.json, DownloadOptions 변환
  context.py            전역 settings / jobs / 이벤트 버스(settings_changed, library_changed, notify)
  theme.py, fonts.py, icons.py
  icon.py               앱 아이콘 드로잉 + assets/icon.png·icon.ico 생성 (`uv run python -m app.icon`)
  window.py             프레임리스 윈도우, 내비게이션, 플레이어 오버레이, 클립보드 감시, 트레이 알림
  widgets/              primitives(Button/Badge/Thumbnail/Skeleton/EmptyState…), player, subtitle_viewer, titlebar, sidebar
  screens/              downloader, playlist, library, settings
assets/fonts/           Geist, Geist Mono, Pretendard (variable TTF)
assets/icons/           QSS에서 참조하는 체크/셰브론 SVG
assets/icon.ico, .png   앱 아이콘 (창·작업 표시줄·트레이, exe 패키징용)
reference/              디자인 레퍼런스 (SKILL.md, 스크린샷)
```

## 디자인 원칙 (reference/SKILL.md 기준)

- **팔레트**: Zinc 계열 뉴트럴 + 단일 액센트(채도 낮춘 로즈 `#e05a72`). 순수 검정·네온 글로우·보라 계열 사용 안 함. 성공/경고는 시맨틱 색으로만 사용.
- **타이포**: Geist(라틴) + Pretendard(한글), 숫자·경로·코드는 Geist Mono.
- **아이콘**: 이모지 금지. 24px 뷰박스, stroke 1.5, round cap으로 통일된 인라인 SVG.
- **레이아웃**: 비대칭 그리드(5:7, 280px:1fr), 좌측 정렬 헤더. 카드는 미디어 타일에만, 목록은 1px 디바이더.
- **상태**: 모든 데이터 영역에 스켈레톤 / 빈 상태 / 인라인 오류.
- **모션**: 화면 전환 시 섹션 순차 페이드, 상태 점 breathing, 토글 스프링 — opacity/transform 계열만.

## 알려진 제약

- 일시중지는 yt-dlp 특성상 "현재 조각까지 받고 멈춤"이며, 재개 시 `.part` 파일에서 이어받습니다.
- 라이브 방송, 회원 전용·연령 제한 영상은 브라우저 쿠키 설정이 필요할 수 있습니다. 믹스(`list=RD...` 자동 재생목록)는 YouTube가 목록을 주지 않아 열 수 없습니다.
- UI 언어 선택은 자리만 있고 현재 한국어만 제공합니다.
- YouTube 영상을 다운로드 없이 앱에서 바로 스트리밍하는 기능은 없습니다. YouTube가 더 이상 영상+음성 합본 스트림을 제공하지 않아 QMediaPlayer로 재생할 수 없기 때문입니다(다운로드 후 재생).
