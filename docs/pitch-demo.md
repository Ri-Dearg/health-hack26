# 피칭 시연 절차 (플랜 v2)

배터리 없는 블루투스 태그(Wiliot 가정)가 위치를 자동으로 기록하고, 직원은 NFC 카드를 대고 버튼 하나만 누른다는 것을 보여주는 시연이다. 실물 브리지가 없으므로 위치 신호는 시뮬레이터가 만든다. 모든 데이터는 지어낸 것이고, 환자는 MRN만 쓴다.

## 화면과 주소

| 화면 | 주소 | 기기 |
|---|---|---|
| 대시보드 (큰 화면) | `/dashboard/` | 노트북 → 프로젝터, 전체 화면 |
| 시뮬레이터 | `/demo/simulator/` | 노트북 다른 창 (시연 내내 열어 둔다: 하트비트를 보냄) |
| 아이템 페이지 (NFC 카드 URL) | `/item/D27/` | 아이폰 Safari |
| 브리지 수신 API | `POST /api/bridge-read/` `{"tag", "location", "seen_at"?, "rssi"?}` | 실제 브리지/웹훅 자리 (시연에서는 안 씀) |

모든 화면은 로그인이 필요하다 (`/admin/login/`). 시뮬레이터는 `is_staff` 계정만. 시드 직원 `aoife`/`ciaran`/`niamh`는 둘 다 된다(비밀번호는 `seed_demo.py`).

## 시작 상태 (`reset_pitch_demo`)

```bash
.venv/bin/python manage.py reset_pitch_demo   # 또는 시뮬레이터의 "Reset demo" 버튼
```

`seed_demo --reset`(장비·대여·대기 명단·위치 로그 전부 삭제 후 재생성) 위에 다음을 얹는다. TagEvent도 지운다. 직원 계정은 그대로 둔다.

| 항목 | 상태 |
|---|---|
| D27 (Manual wheelchair) | Available, OT Store Room, 방금 확인. 태그 `NFC-D27` + `BLUETOOTH-D27` |
| D12 (Manual wheelchair) | On loan (MRN 4100145), Ward 3. 태그 `BLUETOOTH-D12` |
| 기한 지난 대여 | seed_demo의 WCM-002 1건 → Overdue loans = 1 |
| 휠체어 대기 명단 | 4100512 Discharge-critical (Ward 7) · 4100388 Urgent (Ward 3) · 4100601 Routine (Ward 7) |
| **시연에서 입력할 MRN** | **4100273** (대기 명단에 없는 환자) |

구역 이름: `OT Store Room`, `Ward 7`, `Ward 3`, `Return Point` (문자열, `equipment/pitch_demo.py`).

## 시나리오

1. **대여**: 아이폰을 D27 카드에 댄다 → `/item/D27/` → MRN `4100273` 입력 → **Loan to this patient** → "Issued to MRN 4100273 · Due …". 대시보드의 D27이 *On loan*으로 바뀐다(1.5초 폴링).
2. **자동 위치**: 시뮬레이터 **D27 → Ward 7** → 대시보드의 D27 위치가 *Ward 7*, *just now*로 바뀐다. 상태는 그대로 On loan.
3. **반납**: 아이폰을 카드에 다시 댄다 → **Return to store** 하나만 보인다 → 누름 → "Returned · D27 is at Return Point" + **Next patient: MRN 4100512 (Discharge-critical)**. 대시보드는 D27 *Available*, *Return Point*, 오른쪽 Next patient 패널이 초록색으로 강조된다.
4. (선택) **연결 끊김**: 시뮬레이터 **Cut signal: D12** → 약 10초 뒤 대시보드에 빨간 *Disconnected*, Needs locating = 1. **Restore signal: D12** → 바로 사라진다.

페이지를 여는 것(GET)만으로는 아무것도 바뀌지 않는다. 같은 버튼을 두 번 눌러도 대여가 두 개 생기지 않는다("Already on loan to MRN …").

## 설정 키 (`.env`, Render는 Environment)

| 키 | 기본값 | 용도 |
|---|---|---|
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | 터널/LAN 주소를 추가. Render는 자동 추가됨 |
| `CSRF_TRUSTED_ORIGINS` | 비어 있음 | 터널 주소를 `https://` 포함해서. 없으면 아이폰의 Issue/Return POST가 403 |
| `DEVICE_TOKEN` | 비어 있음 | 설정하면 `/api/tag-read/`, `/api/bridge-read/`가 `X-Device-Token` 요구 |
| `BT_DISCONNECT_SECONDS` | 10 | 마지막 브리지 신호가 이보다 오래되면 Disconnected |
| `BT_HEARTBEAT_SECONDS` | 3 | 시뮬레이터 하트비트 간격 |

## 사람이 해야 할 일 (체크리스트)

- [ ] 공개 HTTPS 주소 준비: Render 배포(`render.yaml`) 또는 터널(cloudflared 등). 터널이면 `ALLOWED_HOSTS`·`CSRF_TRUSTED_ORIGINS` 설정
- [ ] Render라면 Shell에서 `python manage.py createsuperuser` 후 `python manage.py reset_pitch_demo`
- [ ] NFC 카드에 NDEF URL 레코드 기록: `https://<host>/item/D27/` (iPhone의 NFC Tools 같은 앱)
- [ ] 아이폰 Safari에서 직원 계정으로 미리 로그인 (`https://<host>/admin/login/`), 카드 태그 → 페이지가 열리는지 확인
- [ ] 아이폰 화면 폭에서 아이템 페이지 레이아웃 확인 (자동 테스트는 HTML만 확인함)
- [ ] 노트북: 대시보드 전체 화면(1920px) + 시뮬레이터 창. 시뮬레이터 창은 시연 내내 열어 둔다 (닫으면 10초 뒤 D27·D12가 Disconnected)
- [ ] 행사장 Wi‑Fi가 기기 간 통신을 막으면 휴대폰 핫스팟으로 전환 (터널/Render는 인터넷만 되면 됨)
- [ ] 시작 전: Reset demo → 시나리오 1~3 리허설 3회 → 마지막에 Reset demo
- [ ] Render 무료 티어: 잠들어 있으면 깨는 데 최대 1분. 시작 10~15분 전에 대시보드를 열어 둔다

## 알려진 한계와 제안 (승인 대기)

- **하트비트 로그 양**: 하트비트는 `record_location`을 그대로 불러 LocationLog에 한 줄씩 쌓는다(append-only 유지). 추적 장비 2대 × 3초 간격 = 분당 약 40줄, 시간당 약 2,400줄. 리셋하면 지워지고 SQLite/Postgres 모두 부담은 없다. 하트비트 줄은 `raw_payload.heartbeat = true`로 표시해서 대시보드 "Recent events"에서는 뺀다.
  - 제안(미구현): 같은 위치의 연속 신호는 LocationLog를 추가하지 않고 장비의 `location_updated_at`만 갱신한다. append-only 규칙(“모든 sighting을 기록”)과 충돌하므로 승인 후에만 적용한다.
- 시뮬레이터의 "끊은 장비" 목록은 그 브라우저 탭의 메모리에만 있다. 시뮬레이터 페이지를 새로 고치면 끊은 신호가 복구된다.
- 대시보드 표는 블루투스 태그가 붙은 장비만 보여준다(시드 기준 8대). 수치 4개는 전체 장비 기준.
- Disconnected는 브리지가 한 번이라도 본 장비에만 계산한다. 시드의 다른 블루투스 장비(WCS/WCM/WCB)는 브리지 신호가 없어서 Signal 칸이 비어 있다.
- 대여 직후 위치는 `issue_item` 규칙대로 "Left the store"(병동을 입력하지 않으면)로 표시되고, 시뮬레이터가 Ward 7로 옮긴다.

## 진행 로그

| 슬라이스 | 결과 |
|---|---|
| 0 기준선 | 테스트 30/30, `makemigrations --check` 변경 없음. 마이그레이션이 필요한 충돌 없음 |
| 1 위치 수신 | `equipment/location.py`(`record_location`, `active_tag`), `POST /api/bridge-read/`. 출처는 기존 `LocationLog.Source.MICROBIT` 재사용(새 값은 마이그레이션 필요) |
| 2 대시보드 | `/dashboard/`, `/dashboard/feed/`, 1.5초 폴링, 바뀐 행 깜빡임 |
| 3 시뮬레이터·리셋 | `/demo/simulator/`, `reset_pitch_demo`, 리허설 3회 자동 테스트 |
| 4 아이템 페이지 | 상태별 버튼 하나, 반납 시 Return Point 기록 + 다음 환자 강조, 위치 확인은 접힌 섹션으로 |
| 5 연결 끊김 | LocationLog에서 계산, 하트비트·Cut/Restore, Needs locating 연결 |
| 6 문서 | 이 문서, `.env.example` |
