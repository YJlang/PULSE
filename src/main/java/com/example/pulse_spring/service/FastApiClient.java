package com.example.pulse_spring.service;

import com.example.pulse_spring.dto.*;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.*;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.util.UriComponentsBuilder;

import java.net.URI;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class FastApiClient {
    private static final Logger log = LoggerFactory.getLogger(FastApiClient.class);

    // FastAPI의 LLM 호출(리뷰 답글/상권 액션)은 수십 초가 걸릴 수 있어 read 타임아웃을 넉넉히 둔 전용 빈을 사용한다.
    // 무한 행(hang)은 막되, 정상 LLM 지연은 허용한다. (config/RestTemplateConfig 참고)
    @Qualifier("fastApiRestTemplate")
    private final RestTemplate restTemplate;

    @Value("${fastapi.base-url}")
    private String fastApiBaseUrl;

    public String sendAnalysisRequest(Long shopId, String shopName, String address, String keyword) {
        Map<String, Object> request = new HashMap<>();
        request.put("shopInfo_name", shopName);
        request.put("shopInfo_address", address);

        try {
            AnalysisTaskResponse response = restTemplate.postForObject(
                    fastApiBaseUrl + "/analysis/request",
                    request,
                    AnalysisTaskResponse.class
            );

            if (response == null || response.getTaskId() == null || response.getTaskId().isBlank()) {
                throw new IllegalStateException("FastAPI가 task id를 반환하지 않았습니다.");
            }

            log.info("FastAPI 분석 요청 전송 완료: shopName={}, taskId={}", shopName, response.getTaskId());
            return response.getTaskId();
        } catch (Exception e) {
            throw new IllegalStateException("AI 분석 요청에 실패했습니다: " + e.getMessage(), e);
        }
    }

    public FastApiReviewSnapshotResponse fetchLatestReviews(String shopName, String address) {
        URI uri = UriComponentsBuilder
                .fromUriString(fastApiBaseUrl + "/reviews/latest")
                .queryParam("store_name", shopName)
                .queryParam("address", address)
                .queryParam("refresh_if_needed", true)
                .queryParam("target_total_reviews", 60)
                .build()
                .encode()
                .toUri();

        try {
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    uri,
                    HttpMethod.GET,
                    null,
                    new ParameterizedTypeReference<>() {}
            );

            Map<String, Object> body = response.getBody();
            if (body == null) {
                return FastApiReviewSnapshotResponse.builder()
                        .storeName(shopName)
                        .address(address)
                        .build();
            }

            List<Map<String, Object>> rawReviews = (List<Map<String, Object>>) body.getOrDefault("reviews", List.of());
            List<ReviewItemDto> reviews = rawReviews.stream()
                    .map(this::mapReviewItem)
                    .toList();

            Map<String, Integer> sourceCounts = new HashMap<>();
            Object sourceCountsRaw = body.get("source_counts");
            if (sourceCountsRaw instanceof Map<?, ?> map) {
                map.forEach((key, value) -> sourceCounts.put(String.valueOf(key), ((Number) value).intValue()));
            }

            return FastApiReviewSnapshotResponse.builder()
                    .storeName((String) body.getOrDefault("store_name", shopName))
                    .address((String) body.getOrDefault("address", address))
                    .totalReviews(((Number) body.getOrDefault("total_reviews", reviews.size())).intValue())
                    .sourceCounts(sourceCounts)
                    .lastCrawledAt((String) body.get("last_crawled_at"))
                    .reviews(reviews)
                    .build();
        } catch (Exception e) {
            return FastApiReviewSnapshotResponse.builder()
                    .storeName(shopName)
                    .address(address)
                    .build();
        }
    }

    public GenerateReviewRepliesResponse generateReviewReplies(String shopName, List<ReviewItemDto> reviews, ReviewManagementSettingsDto settings) {
        Map<String, Object> request = new HashMap<>();
        request.put("shop_name", shopName);
        request.put("reviews", reviews.stream().map(this::toFastApiReviewPayload).toList());
        request.put("settings", toFastApiSettingsPayload(settings));

        try {
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    fastApiBaseUrl + "/reviews/replies/generate",
                    HttpMethod.POST,
                    new HttpEntity<>(request, jsonHeaders()),
                    new ParameterizedTypeReference<>() {}
            );

            Map<String, Object> body = response.getBody();
            if (body == null) {
                return GenerateReviewRepliesResponse.builder().build();
            }

            List<Map<String, Object>> rawReplies = (List<Map<String, Object>>) body.getOrDefault("replies", List.of());
            return GenerateReviewRepliesResponse.builder()
                    .replies(rawReplies.stream().map(this::mapGeneratedReply).toList())
                    .build();
        } catch (Exception e) {
            throw new IllegalStateException("AI 답글 생성에 실패했습니다: " + e.getMessage(), e);
        }
    }

    public Map<String, Object> generateMapInsightActions(MapInsightActionRequest request) {
        try {
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    fastApiBaseUrl + "/map-insight/actions",
                    HttpMethod.POST,
                    new HttpEntity<>(request, jsonHeaders()),
                    new ParameterizedTypeReference<>() {}
            );

            Map<String, Object> body = response.getBody();
            if (body == null) {
                throw new IllegalStateException("FastAPI returned an empty map insight response.");
            }
            return body;
        } catch (Exception e) {
            throw new IllegalStateException("AI 상권 액션 생성에 실패했습니다: " + e.getMessage(), e);
        }
    }

    /**
     * 네이버 DataLab 검색어 트렌드 기반 '오늘의 기회 신호'.
     * 실패 시 null을 반환해 호출부가 해당 섹션만 graceful 처리하도록 한다.
     * 반환 맵: {keyword, signal, intensity, source, period, growthRate}
     */
    public Map<String, Object> fetchSearchSignal(List<String> candidates, String category) {
        Map<String, Object> request = new HashMap<>();
        request.put("candidates", candidates == null ? List.of() : candidates);
        request.put("category", category);

        try {
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    fastApiBaseUrl + "/insights/search-signal",
                    HttpMethod.POST,
                    new HttpEntity<>(request, jsonHeaders()),
                    new ParameterizedTypeReference<>() {}
            );
            return response.getBody();
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * 최신 손님분석(DeepSeek) 결과. 페르소나/매장 요약/키워드 소스로 사용한다.
     * 분석 결과가 없거나(404) 오류면 null.
     * 반환 맵: {store_name, store_summary, personas:[{nickname, tags[], summary, ...}], ...}
     */
    public Map<String, Object> fetchLatestAnalysis() {
        try {
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    fastApiBaseUrl + "/analysis/latest",
                    HttpMethod.GET,
                    null,
                    new ParameterizedTypeReference<>() {}
            );
            return response.getBody();
        } catch (Exception e) {
            return null;
        }
    }

    private HttpHeaders jsonHeaders() {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        return headers;
    }

    private ReviewItemDto mapReviewItem(Map<String, Object> source) {
        return ReviewItemDto.builder()
                .id(String.valueOf(source.getOrDefault("id", "")))
                .author(String.valueOf(source.getOrDefault("author", "리뷰어")))
                .rating(source.get("rating") instanceof Number number ? number.doubleValue() : null)
                .date((String) source.get("date"))
                .content(String.valueOf(source.getOrDefault("text", "")))
                .rawText(String.valueOf(source.getOrDefault("raw_text", "")))
                .hasPhoto(Boolean.TRUE.equals(source.get("has_photo")))
                .source(String.valueOf(source.getOrDefault("source", "")))
                .sourceLabel(String.valueOf(source.getOrDefault("source_label", inferSourceLabel((String) source.get("source")))))
                .build();
    }

    private GeneratedReviewReplyDto mapGeneratedReply(Map<String, Object> source) {
        return GeneratedReviewReplyDto.builder()
                .id(String.valueOf(source.getOrDefault("id", "")))
                .reviewId(String.valueOf(source.getOrDefault("review_id", "")))
                .content(String.valueOf(source.getOrDefault("content", "")))
                .isRecommended(Boolean.TRUE.equals(source.get("is_recommended")))
                .build();
    }

    private Map<String, Object> toFastApiReviewPayload(ReviewItemDto review) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("id", review.getId());
        payload.put("source", review.getSource());
        payload.put("author", review.getAuthor());
        payload.put("rating", review.getRating());
        payload.put("date", review.getDate());
        payload.put("has_photo", review.isHasPhoto());
        payload.put("text", review.getContent());
        payload.put("raw_text", StringUtils.hasText(review.getRawText()) ? review.getRawText() : review.getContent());
        return payload;
    }

    private Map<String, Object> toFastApiSettingsPayload(ReviewManagementSettingsDto settings) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("tone", settings.getTone());
        payload.put("length", settings.getLength());
        payload.put("includeThanks", settings.isIncludeThanks());
        payload.put("includeGreatDay", settings.isIncludeGreatDay());
        payload.put("useEmojis", settings.isUseEmojis());
        payload.put("photoThanks", settings.isPhotoThanks());
        payload.put("brandPreset", settings.getBrandPreset());
        payload.put("optionalInstruction", settings.getOptionalInstruction());
        payload.put("exceptionCases", settings.getExceptionCases());
        return payload;
    }

    private String inferSourceLabel(String source) {
        return "naver".equalsIgnoreCase(source) ? "네이버" : "카카오";
    }
}
