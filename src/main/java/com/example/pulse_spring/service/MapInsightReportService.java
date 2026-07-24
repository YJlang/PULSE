package com.example.pulse_spring.service;

import com.example.pulse_spring.dto.MapInsightReportRequest;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.util.UriComponentsBuilder;

import java.net.URI;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class MapInsightReportService {
    private static final List<CategoryConfig> CATEGORIES = List.of(
            new CategoryConfig("FD6", "음식점"),
            new CategoryConfig("CE7", "카페"),
            new CategoryConfig("CS2", "편의점"),
            new CategoryConfig("HP8", "병원"),
            new CategoryConfig("PM9", "약국"),
            new CategoryConfig("SW8", "지하철역"),
            new CategoryConfig("SC4", "학교"),
            new CategoryConfig("AC5", "학원")
    );

    private static final Map<String, AnchorConfig> ANCHORS = Map.of(
            "SW8", new AnchorConfig(3, "지하철역"),
            "SC4", new AnchorConfig(2, "학교"),
            "AC5", new AnchorConfig(2, "학원"),
            "HP8", new AnchorConfig(1, "병원")
    );

    // Kakao Local 호출용 RestTemplate. 타임아웃은 config/RestTemplateConfig에서 중앙 관리한다.
    private final RestTemplate restTemplate;

    @Value("${kakao.rest-api-key:}")
    private String restApiKey;

    public Map<String, Object> buildReport(MapInsightReportRequest request) {
        if (!StringUtils.hasText(restApiKey)) {
            throw new IllegalStateException("Kakao REST API key is not configured.");
        }

        double latitude = requireCoordinate(request.getLatitude(), "latitude");
        double longitude = requireCoordinate(request.getLongitude(), "longitude");
        int radius = validateRadius(request.getRadius());
        String primaryCategory = StringUtils.hasText(request.getCategory()) ? request.getCategory() : "FD6";

        Map<String, Object> counts = new LinkedHashMap<>();
        Map<String, List<Map<String, Object>>> categoryPlaces = new LinkedHashMap<>();
        Map<String, String> statusesByCategory = new LinkedHashMap<>();
        List<Map<String, Object>> failedCategories = new ArrayList<>();

        for (CategoryConfig category : CATEGORIES) {
            CategorySearchResult result = searchCategory(category, latitude, longitude, radius);
            statusesByCategory.put(category.code(), result.status());

            Map<String, Object> count = new LinkedHashMap<>();
            count.put("label", category.label());
            count.put("total", result.places().size());
            count.put("status", result.status());
            count.put("unavailable", result.failed());
            counts.put(category.code(), count);

            categoryPlaces.put(category.code(), result.places().stream().limit(10).toList());

            if (result.failed()) {
                failedCategories.add(Map.of("code", category.code(), "label", category.label()));
            }
        }

        if (failedCategories.size() == CATEGORIES.size()) {
            throw new IllegalStateException("Kakao category search failed for every category.");
        }

        List<Map<String, Object>> competitors = categoryPlaces.getOrDefault(primaryCategory, List.of()).stream()
                .sorted((left, right) -> Integer.compare(asInt(left.get("distanceM")), asInt(right.get("distanceM"))))
                .limit(5)
                .toList();

        int competitionTotal = getTotal(counts, primaryCategory);
        double areaKm2 = Math.PI * Math.pow(radius / 1000.0, 2);
        double densityPerKm2 = Math.round((competitionTotal / areaKm2) * 10.0) / 10.0;

        List<Map<String, Object>> anchorBreakdown = new ArrayList<>();
        int anchorScore = 0;
        for (Map.Entry<String, AnchorConfig> entry : ANCHORS.entrySet()) {
            int total = getTotal(counts, entry.getKey());
            if (total <= 0) {
                continue;
            }
            AnchorConfig anchor = entry.getValue();
            anchorScore += Math.min(total, 3) * anchor.weight();
            anchorBreakdown.add(Map.of(
                    "categoryGroupCode", entry.getKey(),
                    "label", anchor.label(),
                    "count", total,
                    "weight", anchor.weight()
            ));
        }

        String anchorType = inferAnchorType(counts, anchorScore);
        // AI 마케팅 액션(LLM)은 응답이 수십 초 걸릴 수 있어 리포트와 분리한다.
        // 리포트는 Kakao 기반 상권 데이터만으로 즉시 반환하고,
        // 프론트엔드가 /api/v1/map-insight/actions 를 별도로 호출해 점진적으로 채운다.
        List<Object> actions = List.of();

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("reportState", "ready");
        response.put("center", Map.of("lat", latitude, "lng", longitude));
        response.put("radius", radius);
        response.put("generatedAt", Instant.now().toString());
        response.put("counts", counts);
        response.put("competition", Map.of(
                "categoryGroupCode", primaryCategory,
                "label", "동일 업종(" + getLabel(primaryCategory) + ")",
                "total", competitionTotal,
                "densityPerKm2", densityPerKm2,
                "nearest", competitors
        ));
        response.put("anchors", Map.of(
                "score", anchorScore,
                "typeLabel", anchorType,
                "breakdown", anchorBreakdown
        ));
        response.put("actions", actions);
        response.put("warnings", failedCategories.isEmpty() ? List.of() : List.of(Map.of(
                "type", "partial_failure",
                "title", "일부 데이터가 반영되지 않았습니다",
                "message", "일부 주변 시설 조회에 실패했습니다.",
                "failedCategories", failedCategories
        )));
        response.put("note", failedCategories.isEmpty() ? null : "일부 주변 시설 데이터는 조회 실패로 집계에서 제외했습니다.");
        response.put("_categoryPlaces", categoryPlaces);
        response.put("diagnostics", Map.of("statusesByCategory", statusesByCategory, "failedCategories", failedCategories));
        return response;
    }

    private CategorySearchResult searchCategory(CategoryConfig category, double latitude, double longitude, int radius) {
        List<Map<String, Object>> places = new ArrayList<>();

        for (int page = 1; page <= 3; page++) {
            URI uri = UriComponentsBuilder
                    .fromUriString("https://dapi.kakao.com/v2/local/search/category.json")
                    .queryParam("category_group_code", category.code())
                    .queryParam("x", longitude)
                    .queryParam("y", latitude)
                    .queryParam("radius", radius)
                    .queryParam("page", page)
                    .queryParam("size", 15)
                    .build()
                    .encode()
                    .toUri();

            HttpHeaders headers = new HttpHeaders();
            headers.set(HttpHeaders.AUTHORIZATION, "KakaoAK " + restApiKey);

            try {
                ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                        uri,
                        HttpMethod.GET,
                        new HttpEntity<>(headers),
                        new ParameterizedTypeReference<>() {}
                );
                Map<String, Object> body = response.getBody();
                if (body == null) {
                    return new CategorySearchResult("ERROR", true, places);
                }

                Object documentsRaw = body.get("documents");
                if (documentsRaw instanceof List<?> documents) {
                    for (Object raw : documents) {
                        if (raw instanceof Map<?, ?> item) {
                            places.add(mapPlace(item));
                        }
                    }
                }

                Object metaRaw = body.get("meta");
                boolean isEnd = true;
                if (metaRaw instanceof Map<?, ?> meta && meta.get("is_end") instanceof Boolean value) {
                    isEnd = value;
                }
                if (isEnd) {
                    break;
                }
            } catch (Exception e) {
                return new CategorySearchResult(places.isEmpty() ? "ERROR" : "PARTIAL_ERROR", true, places);
            }
        }

        return new CategorySearchResult(places.isEmpty() ? "ZERO_RESULT" : "OK", false, places);
    }

    private Map<String, Object> mapPlace(Map<?, ?> source) {
        Map<String, Object> place = new LinkedHashMap<>();
        place.put("id", stringValue(source.get("id")));
        place.put("name", stringValue(source.get("place_name")));
        place.put("lat", parseDouble(source.get("y"), 0.0));
        place.put("lng", parseDouble(source.get("x"), 0.0));
        place.put("distanceM", asInt(source.get("distance")));
        place.put("address", StringUtils.hasText(stringValue(source.get("road_address_name")))
                ? stringValue(source.get("road_address_name"))
                : stringValue(source.get("address_name")));
        place.put("phone", stringValue(source.get("phone")));
        place.put("url", stringValue(source.get("place_url")));
        return place;
    }

    private double requireCoordinate(Double value, String fieldName) {
        if (value == null || !Double.isFinite(value)) {
            throw new IllegalArgumentException(fieldName + " is required.");
        }
        return value;
    }

    private int validateRadius(Integer value) {
        int radius = value == null ? 500 : value;
        if (radius != 300 && radius != 500 && radius != 1000) {
            throw new IllegalArgumentException("Unsupported radius.");
        }
        return radius;
    }

    private String inferAnchorType(Map<String, Object> counts, int anchorScore) {
        if (getTotal(counts, "SW8") >= 1) {
            return "역세권형";
        }
        if (getTotal(counts, "SC4") + getTotal(counts, "AC5") >= 5) {
            return "학원가형";
        }
        if (getTotal(counts, "HP8") >= 5) {
            return "의료상권형";
        }
        return anchorScore >= 5 ? "복합상권형" : "일반상권형";
    }

    private int getTotal(Map<String, Object> counts, String categoryCode) {
        Object raw = counts.get(categoryCode);
        if (raw instanceof Map<?, ?> map) {
            return asInt(map.get("total"));
        }
        return 0;
    }

    private String getLabel(String categoryCode) {
        return CATEGORIES.stream()
                .filter(category -> category.code().equals(categoryCode))
                .map(CategoryConfig::label)
                .findFirst()
                .orElse("음식점");
    }

    private String stringValue(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private int asInt(Object value) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            return Integer.parseInt(stringValue(value));
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private double parseDouble(Object value, double fallback) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        try {
            return Double.parseDouble(stringValue(value));
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    private record CategoryConfig(String code, String label) {}
    private record AnchorConfig(int weight, String label) {}
    private record CategorySearchResult(String status, boolean failed, List<Map<String, Object>> places) {}
}
