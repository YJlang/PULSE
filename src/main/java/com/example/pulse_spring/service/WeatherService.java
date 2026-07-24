package com.example.pulse_spring.service;

import com.example.pulse_spring.util.TtlCache;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.util.UriComponentsBuilder;

import java.net.URI;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;

/**
 * 날씨(weatherType) 조회 — 재난안전데이터공유플랫폼 "행정안전부_날씨정보"(DSSP-IF-00183).
 * 전국 250개 시군구의 실시간 날씨를 한 번에 받아, 가게 주소의 시군구로 매칭한다.
 * 하늘상태(SKY_STTS) + 강수형태(PCPTTN_SHP)를 FE WEATHER_TYPES 키로 매핑(스펙 §7), 애니메이션 연동.
 *
 * 키/데이터셋ID 미설정·IP 미등록·오류 시 null을 반환해 대시보드가 graceful 하게 동작한다.
 * ⚠️ safetydata는 호출 서버의 공인 IP를 콘솔에 등록해야 한다(미등록 시 resultCode=32).
 */
@Service
@RequiredArgsConstructor
public class WeatherService {
    private static final Logger log = LoggerFactory.getLogger(WeatherService.class);
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");

    // 시도명 → 법정동 시도코드 2자리 (동명 시군구 disambiguation용). safetydata STDG_SGG_CD 기준(2023+ 개편코드).
    private static final Map<String, String> SIDO_CODE = Map.ofEntries(
            Map.entry("서울", "11"), Map.entry("부산", "26"), Map.entry("대구", "27"),
            Map.entry("인천", "28"), Map.entry("광주", "29"), Map.entry("대전", "30"),
            Map.entry("울산", "31"), Map.entry("세종", "36"), Map.entry("경기", "41"),
            Map.entry("강원", "51"), Map.entry("충북", "43"), Map.entry("충청북", "43"),
            Map.entry("충남", "44"), Map.entry("충청남", "44"), Map.entry("전북", "52"),
            Map.entry("전라북", "52"), Map.entry("전남", "46"), Map.entry("전라남", "46"),
            Map.entry("경북", "47"), Map.entry("경상북", "47"), Map.entry("경남", "48"),
            Map.entry("경상남", "48"), Map.entry("제주", "50")
    );

    // 타임아웃은 config/RestTemplateConfig에서 중앙 관리한다.
    private final RestTemplate restTemplate;
    // 전국 응답을 30분 캐시(데이터는 시간 단위 갱신) → 가게가 많아도 호출 1회로 공유(일 1000건 제한 보호)
    private final TtlCache<List<Map<String, Object>>> bodyCache = new TtlCache<>(30 * 60 * 1000L);

    @Value("${weather.base-url:https://www.safetydata.go.kr/V2/api}")
    private String baseUrl;

    @Value("${weather.api-id:}")
    private String apiId;

    @Value("${weather.service-key:}")
    private String serviceKey;

    /** 가게 주소 기준 weatherType(예: clear_day, rain) 반환. 사용 불가 시 null. */
    public String resolveWeatherType(String address, long nowMs) {
        if (!StringUtils.hasText(address) || !StringUtils.hasText(apiId) || !StringUtils.hasText(serviceKey)) {
            return null;
        }
        List<Map<String, Object>> body = bodyCache.get("nationwide", nowMs, this::fetchBody);
        if (body == null || body.isEmpty()) {
            return null;
        }
        Map<String, Object> district = matchDistrict(body, address);
        if (district == null) {
            log.warn("[Weather] 주소에서 시군구를 매칭하지 못했습니다: {}", address);
            return null;
        }
        int sky = parseInt(district.get("SKY_STTS"), 1);
        int pty = parseInt(district.get("PCPTTN_SHP"), 0);
        boolean day = isDay(LocalDateTime.now(KST).getHour());
        return mapWeatherType(sky, pty, day);
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> fetchBody() {
        try {
            URI uri = UriComponentsBuilder.fromUriString(baseUrl + "/" + apiId)
                    .queryParam("serviceKey", serviceKey)
                    .queryParam("returnType", "json")
                    .queryParam("pageNo", 1)
                    .queryParam("numOfRows", 400) // 전국 시군구 ~250건, 한 페이지로 수신
                    .build(true)
                    .toUri();

            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    uri, HttpMethod.GET, null, new ParameterizedTypeReference<>() {});
            Map<String, Object> bodyMap = response.getBody();
            if (warnIfHeaderError(bodyMap)) {
                return null;
            }
            Object body = bodyMap == null ? null : bodyMap.get("body");
            if (body instanceof List<?> list && (list.isEmpty() || list.get(0) instanceof Map)) {
                return (List<Map<String, Object>>) list;
            }
            log.warn("[Weather] 예상치 못한 응답 형태. keys={}", bodyMap == null ? "null" : bodyMap.keySet());
            return null;
        } catch (Exception e) {
            log.warn("[Weather] 날씨 API 호출 실패: {}", e.getMessage());
            return null;
        }
    }

    /** 가게 주소에 들어있는 시군구명(SGG_NM)으로 매칭. 동명 구는 시도코드로 disambiguation, 가장 구체적인(긴) 이름 우선. */
    private Map<String, Object> matchDistrict(List<Map<String, Object>> body, String address) {
        String addr = address.replaceAll("\\s", "");
        String sidoCode = sidoCodeFromAddress(address);

        Map<String, Object> best = null;
        int bestLen = -1;
        for (Map<String, Object> d : body) {
            String name = String.valueOf(d.getOrDefault("SGG_NM", "")).replaceAll("\\s", "");
            if (name.isEmpty() || !addr.contains(name)) continue;

            String code = String.valueOf(d.getOrDefault("STDG_SGG_CD", ""));
            boolean sidoOk = sidoCode == null || code.startsWith(sidoCode);
            // 시도 일치 + 더 구체적인 이름을 우선. 시도 불일치는 후순위(점수 -100).
            int score = name.length() + (sidoOk ? 100 : 0);
            if (score > bestLen) {
                bestLen = score;
                best = d;
            }
        }
        return best;
    }

    private String sidoCodeFromAddress(String address) {
        String head = address.length() >= 3 ? address.substring(0, 3) : address;
        for (Map.Entry<String, String> e : SIDO_CODE.entrySet()) {
            if (address.startsWith(e.getKey()) || head.startsWith(e.getKey())) {
                return e.getValue();
            }
        }
        return null;
    }

    /** 오류 헤더(resultCode != "00") 감지 시 경고 로그 후 true. */
    @SuppressWarnings("unchecked")
    private boolean warnIfHeaderError(Map<String, Object> body) {
        if (body == null) return false;
        Object header = body.get("header");
        if (header instanceof Map<?, ?> h) {
            Object code = ((Map<String, Object>) h).get("resultCode");
            if (code != null && !"00".equals(String.valueOf(code))) {
                log.warn("[Weather] safetydata 오류 — resultCode={}, msg={}. (IP 미등록=32, 접근거부=20) "
                                + "서버 공인 IP를 safetydata 콘솔의 해당 데이터셋에 등록했는지 확인하세요.",
                        code, ((Map<String, Object>) h).get("errorMsg"));
                return true;
            }
        }
        return false;
    }

    /** SKY_STTS(1맑음/3구름많음/4흐림) + PCPTTN_SHP(0없음/1비/2비눈/3눈/4소나기/5빗방울/6빗방울눈/7눈날림) → weatherType */
    private String mapWeatherType(int sky, int pty, boolean day) {
        return switch (pty) {
            case 1 -> "rain";
            case 2, 6 -> "sleet";
            case 3, 7 -> "snow";
            case 4 -> "shower";
            case 5 -> "drizzle";
            default -> switch (sky) {
                case 3 -> day ? "partly_cloudy_day" : "partly_cloudy_night";
                case 4 -> "cloudy";
                default -> day ? "clear_day" : "clear_night";
            };
        };
    }

    private boolean isDay(int hour) {
        return hour >= 6 && hour < 19;
    }

    private int parseInt(Object value, int fallback) {
        try {
            return (int) Double.parseDouble(String.valueOf(value).trim());
        } catch (Exception e) {
            return fallback;
        }
    }
}
