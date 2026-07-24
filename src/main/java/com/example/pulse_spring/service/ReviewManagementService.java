package com.example.pulse_spring.service;

import com.example.pulse_spring.domain.ReplyTemplate;
import com.example.pulse_spring.domain.ReviewReplySettings;
import com.example.pulse_spring.domain.Shop;
import com.example.pulse_spring.dto.*;
import com.example.pulse_spring.repository.ReplyTemplateRepository;
import com.example.pulse_spring.repository.ReviewReplySettingsRepository;
import com.example.pulse_spring.repository.ShopRepository;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.time.LocalDate;
import java.util.*;

@Service
@RequiredArgsConstructor
public class ReviewManagementService {

    private final ShopRepository shopRepository;
    private final ReviewReplySettingsRepository reviewReplySettingsRepository;
    private final ReplyTemplateRepository replyTemplateRepository;
    private final FastApiClient fastApiClient;
    private final ObjectMapper objectMapper;

    @Transactional(readOnly = true)
    public ReviewManagementContextResponse getContext(String userEmail) {
        Shop shop = getShopByUserEmail(userEmail);
        ReviewManagementSettingsDto settings = getSettings(shop);
        List<ReviewTemplateDto> templates = replyTemplateRepository.findByShopIdOrderByCreatedAtDesc(shop.getId())
                .stream()
                .map(this::toTemplateDto)
                .toList();

        FastApiReviewSnapshotResponse snapshot = fastApiClient.fetchLatestReviews(shop.getName(), shop.getAddress());
        List<ReviewItemDto> sortedReviews = limitLatestReviewsPerSource(snapshot.getReviews(), 30);

        return ReviewManagementContextResponse.builder()
                .storeName(shop.getName())
                .storeAddress(shop.getAddress())
                .summary(buildSummary(snapshot))
                .reviews(sortedReviews)
                .settings(settings)
                .templates(templates)
                .build();
    }

    @Transactional
    public ReviewManagementSettingsDto saveSettings(String userEmail, ReviewManagementSettingsDto request) {
        Shop shop = getShopByUserEmail(userEmail);
        ReviewReplySettings entity = reviewReplySettingsRepository.findByShopId(shop.getId())
                .orElseGet(() -> ReviewReplySettings.builder().shop(shop).build());

        entity.setTone(orDefault(request.getTone(), "친근함"));
        entity.setLengthValue(orDefault(request.getLength(), "보통"));
        entity.setIncludeThanks(request.isIncludeThanks());
        entity.setIncludeGreatDay(request.isIncludeGreatDay());
        entity.setUseEmojis(request.isUseEmojis());
        entity.setPhotoThanks(request.isPhotoThanks());
        entity.setBrandPreset(orDefault(request.getBrandPreset(), ""));
        entity.setBrandPresetsJson(writeJson(request.getBrandPresets()));
        entity.setOptionalInstruction(orDefault(request.getOptionalInstruction(), ""));
        entity.setExceptionCasesJson(writeJson(request.getExceptionCases()));

        return toSettingsDto(reviewReplySettingsRepository.save(entity));
    }

    @Transactional
    public ReviewTemplateDto createTemplate(String userEmail, ReviewTemplateDto request) {
        Shop shop = getShopByUserEmail(userEmail);
        ReplyTemplate entity = ReplyTemplate.builder()
                .shop(shop)
                .name(request.getName())
                .content(request.getContent())
                .tone(orDefault(request.getTone(), "친근함"))
                .lengthValue(orDefault(request.getLength(), "보통"))
                .categoriesJson(writeJson(request.getCategory()))
                .tagsJson(writeJson(request.getTags()))
                .templateDate(parseDate(request.getDate()))
                .build();

        return toTemplateDto(replyTemplateRepository.save(entity));
    }

    @Transactional
    public ReviewTemplateDto updateTemplate(String userEmail, Long templateId, ReviewTemplateDto request) {
        Shop shop = getShopByUserEmail(userEmail);
        ReplyTemplate entity = replyTemplateRepository.findByIdAndShopId(templateId, shop.getId())
                .orElseThrow(() -> new IllegalArgumentException("템플릿을 찾을 수 없습니다."));

        entity.setName(request.getName());
        entity.setContent(request.getContent());
        entity.setTone(orDefault(request.getTone(), entity.getTone()));
        entity.setLengthValue(orDefault(request.getLength(), entity.getLengthValue()));
        entity.setCategoriesJson(writeJson(request.getCategory()));
        entity.setTagsJson(writeJson(request.getTags()));
        entity.setTemplateDate(parseDate(request.getDate()));

        return toTemplateDto(replyTemplateRepository.save(entity));
    }

    @Transactional
    public void deleteTemplate(String userEmail, Long templateId) {
        Shop shop = getShopByUserEmail(userEmail);
        ReplyTemplate entity = replyTemplateRepository.findByIdAndShopId(templateId, shop.getId())
                .orElseThrow(() -> new IllegalArgumentException("템플릿을 찾을 수 없습니다."));
        replyTemplateRepository.delete(entity);
    }

    @Transactional
    public GenerateReviewRepliesResponse generateReplies(String userEmail, GenerateReviewRepliesRequest request) {
        Shop shop = getShopByUserEmail(userEmail);
        ReviewManagementSettingsDto settings = request.getSettings() == null
                ? getSettings(shop)
                : saveSettings(userEmail, request.getSettings());

        return fastApiClient.generateReviewReplies(shop.getName(), request.getReviews(), settings);
    }

    private Shop getShopByUserEmail(String userEmail) {
        return shopRepository.findByUserEmail(userEmail)
                .orElseThrow(() -> new IllegalStateException("사장님 가게 정보를 찾을 수 없습니다."));
    }

    private ReviewManagementSettingsDto getSettings(Shop shop) {
        return reviewReplySettingsRepository.findByShopId(shop.getId())
                .map(this::toSettingsDto)
                .orElseGet(this::defaultSettings);
    }

    private ReviewManagementSettingsDto toSettingsDto(ReviewReplySettings entity) {
        return ReviewManagementSettingsDto.builder()
                .tone(orDefault(entity.getTone(), "친근함"))
                .length(orDefault(entity.getLengthValue(), "보통"))
                .includeThanks(entity.isIncludeThanks())
                .includeGreatDay(entity.isIncludeGreatDay())
                .useEmojis(entity.isUseEmojis())
                .photoThanks(entity.isPhotoThanks())
                .brandPreset(orDefault(entity.getBrandPreset(), ""))
                .brandPresets(readStringList(entity.getBrandPresetsJson()))
                .optionalInstruction(orDefault(entity.getOptionalInstruction(), ""))
                .exceptionCases(readExceptionCases(entity.getExceptionCasesJson()))
                .build();
    }

    private ReviewManagementSettingsDto defaultSettings() {
        return ReviewManagementSettingsDto.builder()
                .tone("친근함")
                .length("보통")
                .includeThanks(true)
                .includeGreatDay(true)
                .useEmojis(false)
                .photoThanks(true)
                .brandPreset("")
                .brandPresets(new ArrayList<>())
                .optionalInstruction("")
                .exceptionCases(defaultExceptionCases())
                .build();
    }

    private ReviewTemplateDto toTemplateDto(ReplyTemplate template) {
        return ReviewTemplateDto.builder()
                .id(template.getId())
                .name(template.getName())
                .content(template.getContent())
                .tone(template.getTone())
                .length(template.getLengthValue())
                .category(readStringList(template.getCategoriesJson()))
                .tags(readStringList(template.getTagsJson()))
                .date(template.getTemplateDate() != null ? template.getTemplateDate().toString() : null)
                .build();
    }

    private ReviewSummaryDto buildSummary(FastApiReviewSnapshotResponse snapshot) {
        List<ReviewItemDto> reviews = snapshot.getReviews();
        int totalReviews = snapshot.getTotalReviews();
        double overallAverage = roundOneDecimal(
                reviews.stream()
                        .map(ReviewItemDto::getRating)
                        .filter(Objects::nonNull)
                        .mapToDouble(Double::doubleValue)
                        .average()
                        .orElse(0.0)
        );

        int naverCount = snapshot.getSourceCounts().getOrDefault("naver", 0);
        int kakaoCount = snapshot.getSourceCounts().getOrDefault("kakao", 0);
        long ratedCount = reviews.stream().filter(review -> review.getRating() != null).count();

        List<ReviewSummaryMetricDto> metrics = List.of(
                buildSourceMetric("네이버 리뷰", "naver", naverCount, totalReviews, reviews),
                buildSourceMetric("카카오 리뷰", "kakao", kakaoCount, totalReviews, reviews),
                ReviewSummaryMetricDto.builder()
                        .name("평점 포함 리뷰")
                        .rating(resolveGrade(ratedCount == 0 ? 0.0 : ((double) ratedCount / Math.max(totalReviews, 1)) * 5))
                        .percentage(toPercent(ratedCount, totalReviews))
                        .reason(String.format("총 %d건 중 %d건에 평점 정보가 포함되어 있습니다.", totalReviews, ratedCount))
                        .build()
        );

        return ReviewSummaryDto.builder()
                .averageRating(overallAverage)
                .totalReviews(totalReviews)
                .evaluationMetrics(metrics)
                .build();
    }

    private List<ReviewItemDto> limitLatestReviewsPerSource(List<ReviewItemDto> reviews, int perSourceLimit) {
        Comparator<ReviewItemDto> byLatestDate = Comparator.comparing(
                ReviewItemDto::getDate,
                Comparator.nullsLast(Comparator.reverseOrder())
        );

        List<ReviewItemDto> naver = reviews.stream()
                .filter(review -> "naver".equalsIgnoreCase(review.getSource()))
                .sorted(byLatestDate)
                .limit(perSourceLimit)
                .toList();

        List<ReviewItemDto> kakao = reviews.stream()
                .filter(review -> "kakao".equalsIgnoreCase(review.getSource()))
                .sorted(byLatestDate)
                .limit(perSourceLimit)
                .toList();

        List<ReviewItemDto> limited = new ArrayList<>();
        limited.addAll(naver);
        limited.addAll(kakao);
        return limited.stream().sorted(byLatestDate).toList();
    }

    private ReviewSummaryMetricDto buildSourceMetric(String name, String source, int count, int totalReviews, List<ReviewItemDto> reviews) {
        double average = roundOneDecimal(
                reviews.stream()
                        .filter(review -> source.equals(review.getSource()) && review.getRating() != null)
                        .mapToDouble(ReviewItemDto::getRating)
                        .average()
                        .orElse(0.0)
        );

        String reason = count == 0
                ? name + " 데이터가 아직 없습니다."
                : average > 0
                ? String.format("%s에서 %d건 수집, 평점 평균 %.1f점입니다.", sourceLabel(source), count, average)
                : String.format("%s에서 %d건 수집했습니다.", sourceLabel(source), count);

        return ReviewSummaryMetricDto.builder()
                .name(name)
                .rating(resolveGrade(average > 0 ? average : ((double) count / Math.max(totalReviews, 1)) * 5))
                .percentage(toPercent(count, totalReviews))
                .reason(reason)
                .build();
    }

    private String resolveGrade(double score) {
        if (score >= 4.5) return "great";
        if (score >= 4.0) return "good";
        if (score >= 3.0) return "soso";
        if (score >= 2.0) return "bad";
        return "worst";
    }

    private int toPercent(long count, int total) {
        if (total <= 0) {
            return 0;
        }
        return (int) Math.round((count * 100.0) / total);
    }

    private double roundOneDecimal(double value) {
        return Math.round(value * 10.0) / 10.0;
    }

    private String sourceLabel(String source) {
        return "naver".equalsIgnoreCase(source) ? "네이버" : "카카오";
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value == null ? List.of() : value);
        } catch (Exception e) {
            throw new IllegalStateException("JSON 직렬화에 실패했습니다.", e);
        }
    }

    private List<String> readStringList(String json) {
        if (!StringUtils.hasText(json)) {
            return new ArrayList<>();
        }
        try {
            return objectMapper.readValue(json, new TypeReference<>() {});
        } catch (Exception e) {
            return new ArrayList<>();
        }
    }

    private List<Map<String, Object>> readExceptionCases(String json) {
        if (!StringUtils.hasText(json)) {
            return defaultExceptionCases();
        }
        try {
            return objectMapper.readValue(json, new TypeReference<>() {});
        } catch (Exception e) {
            return defaultExceptionCases();
        }
    }

    private String orDefault(String value, String defaultValue) {
        return StringUtils.hasText(value) ? value : defaultValue;
    }

    private LocalDate parseDate(String value) {
        if (!StringUtils.hasText(value)) {
            return LocalDate.now();
        }
        return LocalDate.parse(value);
    }

    private List<Map<String, Object>> defaultExceptionCases() {
        List<Map<String, Object>> defaults = new ArrayList<>();
        defaults.add(new LinkedHashMap<>(Map.of(
                "id", "hygiene",
                "type", "위생",
                "keywords", List.of("머리카락", "이물질", "깨끗하지", "청결", "벌레"),
                "empathy", "불쾌하셨을 고객님의 마음을 충분히 이해합니다",
                "apology", "위생 관리에 소홀했던 점 깊이 사과드립니다",
                "solution", "즉시 새로운 음식으로 교체해드리겠으며, 위생 관리를 더욱 철저히 하겠습니다",
                "enabled", false
        )));
        defaults.add(new LinkedHashMap<>(Map.of(
                "id", "service",
                "type", "서비스",
                "keywords", List.of("불친절", "무시", "주문 누락", "오래 기다림", "태도"),
                "empathy", "불편을 드려 정말 죄송합니다",
                "apology", "직원 교육이 부족했던 점 사과드립니다",
                "solution", "서비스 개선을 위해 직원 교육을 강화하고, 다음 방문 시 더 나은 경험을 제공하겠습니다",
                "enabled", false
        )));
        defaults.add(new LinkedHashMap<>(Map.of(
                "id", "food-quality",
                "type", "음식품질",
                "keywords", List.of("차갑게", "맛없", "짜", "싱거", "익지 않", "탔"),
                "empathy", "기대에 미치지 못해 죄송합니다",
                "apology", "품질 관리에 소홀했던 점 사과드립니다",
                "solution", "즉시 새로 조리해드리겠으며, 맛과 품질 관리에 더욱 신경 쓰겠습니다",
                "enabled", false
        )));
        defaults.add(new LinkedHashMap<>(Map.of(
                "id", "price-portion",
                "type", "가격/양",
                "keywords", List.of("비싸", "양이 적", "가성비", "가격 대비"),
                "empathy", "고객님의 소중한 의견 감사드립니다",
                "apology", "기대에 못 미쳐 아쉽습니다",
                "solution", "메뉴 구성과 가격 정책을 지속적으로 개선하도록 노력하겠습니다",
                "enabled", false
        )));
        return defaults;
    }
}
