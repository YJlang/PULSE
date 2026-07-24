package com.example.pulse_spring.service;

import com.example.pulse_spring.dto.Coordinates;
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
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Service
@RequiredArgsConstructor
public class KakaoLocalClient {
    private final RestTemplate restTemplate;

    @Value("${kakao.rest-api-key:}")
    private String restApiKey;

    public Optional<Coordinates> geocodeAddress(String address) {
        if (!StringUtils.hasText(restApiKey) || !StringUtils.hasText(address)) {
            return Optional.empty();
        }

        URI uri = UriComponentsBuilder
                .fromUriString("https://dapi.kakao.com/v2/local/search/address.json")
                .queryParam("query", address)
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
                return Optional.empty();
            }

            Object documentsRaw = body.get("documents");
            if (!(documentsRaw instanceof List<?> documents) || documents.isEmpty()) {
                return Optional.empty();
            }

            Object firstRaw = documents.get(0);
            if (!(firstRaw instanceof Map<?, ?> first)) {
                return Optional.empty();
            }

            Double longitude = parseDouble(first.get("x"));
            Double latitude = parseDouble(first.get("y"));
            if (latitude == null || longitude == null) {
                return Optional.empty();
            }

            return Optional.of(new Coordinates(latitude, longitude));
        } catch (Exception ignored) {
            return Optional.empty();
        }
    }

    private Double parseDouble(Object value) {
        if (value == null) {
            return null;
        }

        try {
            return Double.parseDouble(String.valueOf(value));
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
